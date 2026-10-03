"""Alertas urgentes no celular via ntfy.

O que conta como urgente:
  1. o pedido direto "me avisa no celular ..." (ferramenta avisar_celular);
  2. lembrete criado como urgente/importante (criar_lembrete com urgente=sim, ou com "urgente" no texto);
  3. e-mail novo de um remetente listado em ALERT_EMAIL_SENDERS (conferido a cada ALERT_EMAIL_INTERVAL s).

Alerta urgente vai com prioridade máxima (5) no ntfy. Configuração no .env:
  NTFY_TOPIC (e NTFY_SERVER opcional)
O conteúdo de e-mail é de terceiros: só vira texto do alerta, nunca passa pelo modelo nem dispara outra ação.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re

import httpx
from fastapi import APIRouter

from .memory import DATA_DIR

SEEN_FILE = DATA_DIR / "alertas_emails_vistos.json"
_task = {}


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def backend() -> str:
    if _env("NTFY_TOPIC"):
        return "ntfy"
    return ""


def _clean(s, limit: int) -> str:
    return " ".join(str(s or "").split())[:limit]


async def push(title: str, message: str, urgent: bool = True) -> str:
    """Manda o alerta. Devolve uma frase curta sobre o resultado (nunca levanta exceção)."""
    title, message = _clean(title, 250) or "Jarvis", _clean(message, 1000) or "Alerta do Jarvis."
    kind = backend()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            if kind == "ntfy":
                url = f"{_env('NTFY_SERVER', 'https://ntfy.sh').rstrip('/')}/{_env('NTFY_TOPIC')}"
                headers = {"Title": title.encode("utf-8"), "Priority": "5" if urgent else "3", "Tags": "rotating_light"}
                r = await client.post(url, content=message.encode("utf-8"), headers=headers)
                if r.status_code >= 300:
                    return f"O ntfy recusou o alerta ({r.status_code})."
            else:
                return "Alerta no celular não configurado: falta NTFY_TOPIC no .env."
    except (httpx.HTTPError, ValueError) as e:
        return f"Não consegui mandar o alerta ({type(e).__name__})."
    print(f"[jarvis] alerta no celular ({kind}): {title}: {message}")
    return f"Alerta enviado ao celular via {kind}{' (urgente)' if urgent else ''}."


# ---- lembretes urgentes (chamado por reminders.fire_due) ----
def is_urgent(item: dict) -> bool:
    return bool(item.get("urgente")) or bool(re.search(r"\b(urgente|importante)\b", str(item.get("texto", "")).lower()))


# ---- e-mails de remetentes listados ----
def _senders() -> list:
    return [s.strip().lower() for s in _env("ALERT_EMAIL_SENDERS").split(",") if s.strip()]


def _load_seen() -> list:
    try:
        return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []


def _save_seen(seen: list) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SEEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(seen[-300:]), encoding="utf-8")
    tmp.replace(SEEN_FILE)
    os.chmod(SEEN_FILE, 0o600)


async def check_emails() -> int:
    from . import google_data  # importado aqui para não pesar quando não há remetentes configurados
    senders = _senders()
    if not senders or not backend() or not google_data.connected():
        return 0
    q = "is:unread newer_than:1d from:(" + " OR ".join(senders) + ")"
    mails = await google_data.important_emails(q, 10)
    fresh = not SEEN_FILE.exists()  # primeira vez: só marca como visto, sem alertar o que já estava lá
    seen = _load_seen()
    new = []
    for m in mails:
        key = hashlib.sha256(f"{m['email']}|{m['assunto']}|{m['quando'].isoformat()}".encode()).hexdigest()[:16]
        if key not in seen:
            seen.append(key)
            new.append(m)
    if new:
        _save_seen(seen)
    if fresh:
        _save_seen(seen)
        return 0
    for m in new:  # assunto e remetente são de terceiros: só viram texto do alerta
        await push(f"E-mail de {_clean(m['de'], 60)}", _clean(m["assunto"], 200))
    return len(new)


async def email_loop() -> None:
    every = max(60, int(_env("ALERT_EMAIL_INTERVAL", "120")))
    await asyncio.sleep(15)
    while True:
        try:
            await check_emails()
        except Exception as e:
            print(f"[jarvis] erro ao conferir e-mails urgentes: {type(e).__name__}: {e}")
        await asyncio.sleep(every)


router = APIRouter()


@router.on_event("startup")
async def _start():
    _task["email"] = asyncio.create_task(email_loop())


@router.on_event("shutdown")
async def _stop():
    if _task.get("email"):
        _task["email"].cancel()


@router.get("/api/alertas/estado")
def status():
    return {"canal": backend() or None, "remetentes_vigiados": len(_senders())}


# ---- ferramenta exposta ao modelo (registrada em tools.TOOLS) ----
async def avisar_celular(mensagem: str, urgente="sim") -> str:
    urgent = str(urgente).strip().lower() not in ("nao", "não", "n", "false", "0", "no")
    return await push("Jarvis", mensagem, urgent)


TOOLS = {
    "avisar_celular": {"fn": avisar_celular, "efeito": True, "web": False,
                       "desc": 'manda um alerta para o celular do usuário agora (via ntfy). Use só quando o usuário pedir "me avisa no celular" '
                               'ou algo claramente urgente. args: {"mensagem": "texto curto", "urgente": "sim"}',
                       "params": {"mensagem": "o aviso, curto e direto",
                                  "urgente": "sim (prioridade máxima) ou nao (notificação comum)"}},
}
