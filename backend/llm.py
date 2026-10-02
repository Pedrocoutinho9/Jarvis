"""Camada de LLM: troque de provedor mudando LLM_PROVIDER no .env (gemini ou ollama)."""
import os

import httpx


class LLMError(Exception):
    """Erro com mensagem já pronta para mostrar ao usuário."""


async def ask(messages: list, system: str) -> str:
    provider = os.getenv("LLM_PROVIDER", "gemini").lower()
    if provider == "gemini":
        return await _gemini(messages, system)
    if provider == "ollama":
        return await _ollama(messages, system)
    raise LLMError(f"LLM_PROVIDER inválido: '{provider}'. Use gemini ou ollama.")


async def _gemini(messages: list, system: str) -> str:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise LLMError("Falta a GEMINI_API_KEY no arquivo .env.")
    model = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview").strip()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [
            {"role": "model" if m["role"] == "assistant" else "user",
             "parts": [{"text": m["content"]}]}
            for m in messages
        ],
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, headers={"x-goog-api-key": key}, json=body)
    if r.status_code == 429:
        raise LLMError("Limite gratuito do Gemini atingido. Espere um minuto e tente de novo.")
    if r.status_code == 404:
        raise LLMError(f"Modelo '{model}' não encontrado. Ajuste GEMINI_MODEL no .env.")
    if r.status_code in (400, 401, 403):
        raise LLMError(f"O Gemini recusou a chamada ({r.status_code}). Confira a GEMINI_API_KEY e o GEMINI_MODEL.")
    r.raise_for_status()
    try:
        parts = r.json()["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError):
        raise LLMError("O Gemini devolveu uma resposta vazia. Tente reformular a pergunta.")


async def _ollama(messages: list, system: str) -> str:
    host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    model = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
    body = {"model": model, "stream": False,
            "messages": [{"role": "system", "content": system}] + messages}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(f"{host}/api/chat", json=body)
    except httpx.ConnectError:
        raise LLMError("Não consegui falar com o Ollama. Abra o app Ollama e tente de novo.")
    if r.status_code == 404:
        raise LLMError(f"Modelo não baixado. No terminal, rode: ollama pull {model}")
    r.raise_for_status()
    try:
        return r.json()["message"]["content"].strip()
    except KeyError:
        raise LLMError("O Ollama devolveu uma resposta vazia.")
