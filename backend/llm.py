"""Camada de LLM: troque de provedor mudando LLM_PROVIDER no .env."""

import json
import os
import re

import httpx
from dotenv import load_dotenv


# Carrega as variáveis do arquivo .env
load_dotenv()


class LLMError(Exception):
    """Erro com mensagem pronta para mostrar ao usuário."""


def native_tools() -> bool:
    """Ollama entende ferramentas nativamente (o Qwen3 foi treinado para isso); os outros usam marcadores."""
    return (os.getenv("LLM_PROVIDER", "gemini").strip().lower() == "ollama"
            and os.getenv("OLLAMA_NATIVE_TOOLS", "on").strip().lower() != "off")


async def stream(messages: list, system: str, tool_specs=None):
    """Entrega a resposta em pedaços assim que o modelo gera (para a voz começar antes).
    Pedaços são texto (str) ou, com ferramentas nativas, {"tool": nome, "args": {...}}."""
    if os.getenv("LLM_PROVIDER", "gemini").strip().lower() == "ollama":
        async for piece in _ollama_stream(messages, system, tool_specs):
            yield piece
    else:  # Gemini: sem streaming, entrega tudo de uma vez
        yield await ask(messages, system)


async def ask(messages: list, system: str) -> str:
    provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()

    if provider == "gemini":
        return await _gemini(messages, system)

    if provider == "ollama":
        return await _ollama(messages, system)

    raise LLMError(
        f"LLM_PROVIDER inválido: '{provider}'. "
        "Use 'gemini' ou 'ollama'."
    )


# ============================================================
# GEMINI
# ============================================================

async def _gemini(messages: list, system: str) -> str:
    key = os.getenv("GEMINI_API_KEY", "").strip()

    if not key:
        raise LLMError(
            "Falta a GEMINI_API_KEY no arquivo .env."
        )

    model = os.getenv(
        "GEMINI_MODEL",
        "gemini-3-flash-preview"
    ).strip()

    url = (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{model}:generateContent"
    )

    body = {
        "system_instruction": {
            "parts": [
                {
                    "text": system
                }
            ]
        },
        "contents": [
            {
                "role": (
                    "model"
                    if message["role"] == "assistant"
                    else "user"
                ),
                "parts": [
                    {
                        "text": message["content"]
                    }
                ],
            }
            for message in messages
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                url,
                headers={
                    "x-goog-api-key": key,
                    "Content-Type": "application/json",
                },
                json=body,
            )

    except httpx.TimeoutException:
        raise LLMError(
            "Tempo esgotado ao consultar o Gemini."
        )

    except httpx.RequestError as error:
        print("=== ERRO DE CONEXÃO COM GEMINI ===")
        print(error)
        print("==================================")

        raise LLMError(
            f"Erro de conexão com o Gemini: {error}"
        )

    # --------------------------------------------------------
    # Mostra no terminal o erro REAL retornado pelo Gemini.
    # Isso ajuda a descobrir a causa de um eventual HTTP 502.
    # --------------------------------------------------------

    if response.status_code != 200:

        print()
        print("========================================")
        print("           ERRO GEMINI")
        print("========================================")
        print("Status HTTP:", response.status_code)
        print("Resposta:", response.text)
        print("========================================")
        print()

        if response.status_code == 400:
            raise LLMError(
                "O Gemini rejeitou a requisição (400). "
                "Confira o formato da requisição e o modelo."
            )

        if response.status_code == 401:
            raise LLMError(
                "A GEMINI_API_KEY é inválida ou não foi aceita."
            )

        if response.status_code == 403:
            raise LLMError(
                "O Gemini recusou o acesso (403). "
                "Verifique a API Key, o projeto e as permissões."
            )

        if response.status_code == 404:
            raise LLMError(
                f"Modelo '{model}' não encontrado."
            )

        if response.status_code == 429:
            raise LLMError(
                "Limite de uso do Gemini atingido. "
                "Tente novamente mais tarde."
            )

        raise LLMError(
            f"Gemini retornou HTTP {response.status_code}."
        )

    # --------------------------------------------------------
    # Processa a resposta do Gemini
    # --------------------------------------------------------

    try:
        data = response.json()

        candidates = data.get("candidates", [])

        if not candidates:
            print("Resposta inesperada do Gemini:")
            print(response.text)

            raise LLMError(
                "O Gemini não retornou nenhum candidato de resposta."
            )

        content = candidates[0].get("content", {})
        parts = content.get("parts", [])

        text = "".join(
            part.get("text", "")
            for part in parts
        ).strip()

        if not text:
            print("Resposta vazia do Gemini:")
            print(response.text)

            raise LLMError(
                "O Gemini devolveu uma resposta vazia."
            )

        return text

    except ValueError:
        print("O Gemini retornou algo que não é JSON:")
        print(response.text)

        raise LLMError(
            "O Gemini devolveu uma resposta inválida."
        )


# ============================================================
# OLLAMA
# ============================================================

def _think():
    # on/off para Qwen3; gpt-oss não desliga o raciocínio e aceita low/medium/high
    value = os.getenv("OLLAMA_THINK", "off").strip().lower()
    return value if value in ("low", "medium", "high") else value == "on"


def _ollama_body(messages: list, system: str, stream: bool) -> tuple:
    host = os.getenv(
        "OLLAMA_HOST",
        "http://localhost:11434"
    ).strip()

    model = os.getenv(
        "OLLAMA_MODEL",
        "qwen3:8b"
    ).strip()

    body = {
        "model": model,
        "stream": stream,
        # Modelos que "pensam" (Qwen3) ficam lentos para voz; desligado por padrão.
        "think": _think(),
        # Mantém o modelo carregado na memória entre perguntas.
        "keep_alive": os.getenv("OLLAMA_KEEP_ALIVE", "30m").strip(),
        # O padrão do Ollama (4096) corta o início do prompt quando entra resultado de pesquisa.
        "options": {"num_ctx": int(os.getenv("OLLAMA_NUM_CTX", "8192"))},
        "messages": [
            {
                "role": "system",
                "content": system
            }
        ] + messages,
    }
    return host, model, body


async def _ollama_stream(messages: list, system: str, tool_specs=None):
    host, model, body = _ollama_body(messages, system, True)
    if tool_specs:
        body["tools"] = tool_specs
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", f"{host}/api/chat", json=body) as response:
                if response.status_code == 404:
                    raise LLMError(
                        f"Modelo '{model}' não baixado. "
                        f"No terminal, rode: ollama pull {model}"
                    )
                if response.status_code != 200:
                    print("=== ERRO OLLAMA ===", response.status_code, await response.aread())
                    raise LLMError(f"Ollama retornou HTTP {response.status_code}.")
                async for row in response.aiter_lines():
                    if not row.strip():
                        continue
                    data = json.loads(row)
                    piece = data.get("message", {}).get("content", "")
                    if piece:
                        yield piece
                    for call in data.get("message", {}).get("tool_calls") or []:
                        fn = call.get("function", {})
                        yield {"tool": fn.get("name", ""), "args": fn.get("arguments") or {}}
                    if data.get("done"):
                        break
    except httpx.ConnectError:
        raise LLMError(
            "Não consegui falar com o Ollama. "
            "Abra o app Ollama e tente de novo."
        )
    except httpx.TimeoutException:
        raise LLMError("O Ollama demorou demais para responder.")
    except httpx.RequestError as error:
        raise LLMError(f"Erro de conexão com o Ollama: {error}")


async def _ollama(messages: list, system: str) -> str:
    host, model, body = _ollama_body(messages, system, False)

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{host}/api/chat",
                json=body,
            )

    except httpx.ConnectError:
        raise LLMError(
            "Não consegui falar com o Ollama. "
            "Abra o app Ollama e tente de novo."
        )

    except httpx.TimeoutException:
        raise LLMError(
            "O Ollama demorou demais para responder."
        )

    except httpx.RequestError as error:
        print("=== ERRO OLLAMA ===")
        print(error)
        print("===================")

        raise LLMError(
            f"Erro de conexão com o Ollama: {error}"
        )

    if response.status_code == 404:
        raise LLMError(
            f"Modelo '{model}' não baixado. "
            f"No terminal, rode: ollama pull {model}"
        )

    if response.status_code != 200:
        print()
        print("========================================")
        print("           ERRO OLLAMA")
        print("========================================")
        print("Status HTTP:", response.status_code)
        print("Resposta:", response.text)
        print("========================================")
        print()

        raise LLMError(
            f"Ollama retornou HTTP {response.status_code}."
        )

    try:
        data = response.json()

        text = (
            data
            .get("message", {})
            .get("content", "")
        )
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()

        if not text:
            raise LLMError(
                "O Ollama devolveu uma resposta vazia."
            )

        return text

    except ValueError:
        raise LLMError(
            "O Ollama devolveu uma resposta inválida."
        )