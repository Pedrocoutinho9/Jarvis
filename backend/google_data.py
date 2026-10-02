"""Google Agenda e Gmail, somente leitura, com OAuth local (sem bibliotecas do Google: só httpx).

Credencial do cliente e token ficam em ~/Library/Application Support/Jarvis (ou JARVIS_DATA_DIR),
fora do repositório. Escopos: calendar.readonly e gmail.readonly. Nada aqui envia, apaga ou altera.

"E-mails importantes" = não lidos, das últimas 24 h, marcados como Importantes pelo Gmail,
fora de Promoções e Social (ajustável em GMAIL_QUERY).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta
from email.utils import parseaddr
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .memory import DATA_DIR

CLIENT_FILE = DATA_DIR / "google_client.json"
TOKEN_FILE = DATA_DIR / "google_token.json"
SCOPES = "https://www.googleapis.com/auth/calendar.readonly https://www.googleapis.com/auth/gmail.readonly"
GMAIL_QUERY = os.getenv("GMAIL_QUERY", "is:unread is:important newer_than:1d -category:promotions -category:social")
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CAL = "https://www.googleapis.com/calendar/v3"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
WEEK = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]

_pending: dict = {}   # state -> (verificador PKCE, redirect_uri)
_cache: dict = {}     # chave -> (instante, dado)
_lock = asyncio.Lock()


class NotConnected(Exception):
    pass


def _write(path, data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _client() -> dict:
    try:
        raw = json.loads(CLIENT_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        raise NotConnected("credencial do Google ausente")
    c = raw.get("installed") or raw.get("web") or raw
    if not c.get("client_id") or not c.get("client_secret"):
        raise NotConnected("credencial do Google inválida")
    return c


def connected() -> bool:
    return TOKEN_FILE.exists() and CLIENT_FILE.exists()


async def _access_token() -> str:
    async with _lock:
        try:
            tok = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            raise NotConnected("Google não conectado")
        if tok.get("access_token") and tok.get("expires_at", 0) > time.time() + 60:
            return tok["access_token"]
        c = _client()
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(TOKEN_URL, data={
                "client_id": c["client_id"], "client_secret": c["client_secret"],
                "refresh_token": tok.get("refresh_token", ""), "grant_type": "refresh_token"})
        if r.status_code in (400, 401):  # token revogado ou vencido (app em modo Teste vence em 7 dias)
            TOKEN_FILE.unlink(missing_ok=True)
            raise NotConnected("acesso ao Google expirou")
        r.raise_for_status()
        d = r.json()
        tok.update(access_token=d["access_token"], expires_at=time.time() + d.get("expires_in", 3600))
        _write(TOKEN_FILE, tok)
        return tok["access_token"]


async def _get(client: httpx.AsyncClient, url: str, params=None) -> dict:
    r = await client.get(url, params=params, headers={"Authorization": f"Bearer {await _access_token()}"})
    r.raise_for_status()
    return r.json()


async def _cached(key: str, ttl: int, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    data = await fn()
    _cache[key] = (time.time(), data)
    return data


# ---- dados ----
def _parse(when: dict):
    """Devolve (datetime local, dia_inteiro)."""
    if "dateTime" in when:
        return datetime.fromisoformat(when["dateTime"].replace("Z", "+00:00")).astimezone(), False
    return datetime.fromisoformat(when["date"]).astimezone(), True


async def events(days: int = 1) -> list:
    """Compromissos de hoje até `days` dias à frente, de todas as agendas marcadas no Google Agenda."""
    days = max(1, min(int(days), 14))

    async def fetch():
        start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=days)
        async with httpx.AsyncClient(timeout=15) as client:
            cals = await _get(client, f"{CAL}/users/me/calendarList", {"minAccessRole": "reader"})
            ids = [c["id"] for c in cals.get("items", []) if c.get("selected") or c.get("primary")]
            results = await asyncio.gather(*(
                _get(client, f"{CAL}/calendars/{quote(cid, safe='')}/events",
                     {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true",
                      "orderBy": "startTime", "maxResults": 50})
                for cid in ids), return_exceptions=True)
        out, seen = [], set()
        for res in results:
            if isinstance(res, Exception):
                continue
            for e in res.get("items", []):
                if e.get("status") == "cancelled" or e.get("id") in seen:
                    continue
                me = next((a for a in e.get("attendees", []) if a.get("self")), None)
                if me and me.get("responseStatus") == "declined":
                    continue
                seen.add(e.get("id"))
                ini, allday = _parse(e["start"])
                fim, _ = _parse(e["end"])
                out.append({"titulo": e.get("summary") or "(sem título)", "inicio": ini, "fim": fim,
                            "dia_inteiro": allday, "local": e.get("location", "")})
        return sorted(out, key=lambda x: (x["inicio"], not x["dia_inteiro"]))

    return await _cached(f"agenda{days}", 120, fetch)


async def important_emails(query: str = "", n: int = 5) -> list:
    q = query.strip() or GMAIL_QUERY
    n = max(1, min(int(n), 10))

    async def fetch():
        async with httpx.AsyncClient(timeout=15) as client:
            lst = await _get(client, f"{GMAIL}/messages", {"q": q, "maxResults": n})
            msgs = await asyncio.gather(*(
                _get(client, f"{GMAIL}/messages/{m['id']}",
                     [("format", "metadata"), ("metadataHeaders", "From"), ("metadataHeaders", "Subject")])
                for m in lst.get("messages", [])), return_exceptions=True)
        out = []
        for m in msgs:
            if isinstance(m, Exception):
                continue
            h = {x["name"].lower(): x["value"] for x in m.get("payload", {}).get("headers", [])}
            name, addr = parseaddr(h.get("from", ""))
            out.append({"de": name or addr or "desconhecido", "email": addr,
                        "assunto": h.get("subject") or "(sem assunto)",
                        "trecho": html.unescape(m.get("snippet", ""))[:200],
                        "quando": datetime.fromtimestamp(int(m.get("internalDate", 0)) / 1000).astimezone(),
                        "nao_lido": "UNREAD" in m.get("labelIds", [])})
        return out

    return await _cached(f"mail{q}{n}", 120, fetch)


# ---- texto ----
def _hm(d: datetime) -> str:
    return f"{d.hour}h" + (f"{d.minute:02d}" if d.minute else "")


def _clean(s: str, limit: int = 80) -> str:
    """Tira ':' (o HUD usa como separador) e encurta."""
    s = re.sub(r"\s+", " ", s.replace(":", " -")).strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _day(d: datetime) -> str:
    delta = (d.date() - datetime.now().date()).days
    return {0: "hoje", 1: "amanhã"}.get(delta, f"{WEEK[d.weekday()]} {d.day:02d}/{d.month:02d}")


def _event_line(e: dict, with_day: bool = False) -> str:
    when = "o dia todo" if e["dia_inteiro"] else f"{_hm(e['inicio'])} às {_hm(e['fim'])}"
    pre = _day(e["inicio"]) + ", " if with_day else ""
    loc = f" ({e['local']})" if e["local"] else ""
    return f"{pre}{when}: {e['titulo']}{loc}"


SETUP_HTML = '<p>Google não conectado. Abra <a href="/google/conectar">/google/conectar</a>.</p>'


async def agenda_card() -> dict:
    base = {"id": "agenda", "icon": "calendar", "k": "Hoje"}
    try:
        evs = await events(1)
    except NotConnected:
        return {**base, "t": "Agenda desconectada", "html": SETUP_HTML,
                "say": "A agenda do Google ainda não está conectada. O endereço de conexão está na tela."}
    except Exception as e:
        print(f"[jarvis] agenda falhou: {type(e).__name__}: {e}")
        return {**base, "t": "Agenda", "html": "<p>Não consegui ler a agenda agora.</p>",
                "say": "Não consegui ler a sua agenda agora."}
    if not evs:
        return {**base, "t": "Agenda livre", "html": "<p>Nenhum compromisso hoje.</p>",
                "say": "A agenda de hoje está livre. Um dia inteiro sem desculpas."}
    now = datetime.now().astimezone()
    lis = "".join(f"<li>{'dia todo' if e['dia_inteiro'] else _hm(e['inicio'])} · {html.escape(_clean(e['titulo']))}</li>"
                  for e in evs[:6])
    nxt = next((e for e in evs if not e["dia_inteiro"] and e["fim"] > now), None)
    n = len(evs)
    say = f"Hoje há {n} compromisso{'s' if n > 1 else ''} na agenda."
    if nxt:
        verb = "Agora está rolando" if nxt["inicio"] <= now else f"O próximo é às {_hm(nxt['inicio'])}"
        say += f" {verb}: {_clean(nxt['titulo'])}."
    else:
        say += " Os de horário marcado já passaram."
    return {**base, "t": f"{n} compromisso{'s' if n > 1 else ''}", "html": f"<ul>{lis}</ul>", "say": say}


async def email_card() -> dict:
    base = {"id": "email", "icon": "mail", "k": "E-mails"}
    try:
        mails = await important_emails()
    except NotConnected:
        return {**base, "t": "Gmail desconectado", "html": SETUP_HTML,
                "say": "O Gmail ainda não está conectado."}
    except Exception as e:
        print(f"[jarvis] gmail falhou: {type(e).__name__}: {e}")
        return {**base, "t": "Gmail", "html": "<p>Não consegui ler os e-mails agora.</p>",
                "say": "Não consegui ler os seus e-mails agora."}
    if not mails:
        return {**base, "t": "Nada urgente", "html": "<p>Nenhum e-mail importante não lido nas últimas 24 horas.</p>",
                "say": "Nenhum e-mail importante esperando por você. Ninguém precisa de você hoje, aparentemente."}
    lis = "".join(f"<li>{html.escape(_clean(m['de'], 30))} · {html.escape(_clean(m['assunto'], 70))}</li>"
                  for m in mails)
    n = len(mails)
    first = mails[0]
    say = (f"{n} e-mail{'s' if n > 1 else ''} importante{'s' if n > 1 else ''} não lido{'s' if n > 1 else ''}. "
           f"O mais recente é de {_clean(first['de'], 40)}, sobre {_clean(first['assunto'], 80)}.")
    return {**base, "t": "Pedem sua atenção", "html": f"<ul>{lis}</ul>", "say": say}


# ---- ferramentas para o modelo (registradas em tools.TOOLS) ----
NOT_CONNECTED = ("O Google não está conectado. Diga ao usuário para abrir http://127.0.0.1:8000/google/conectar "
                 "no navegador e autorizar o acesso.")


async def ver_agenda(dias="1") -> str:
    try:
        days = int(float(dias))
    except (TypeError, ValueError):
        days = 1
    try:
        evs = await events(days)
    except NotConnected:
        return NOT_CONNECTED
    except httpx.HTTPError as e:
        return f"Não consegui ler a agenda ({e.__class__.__name__})."
    if not evs:
        return f"Nenhum compromisso de hoje até {max(1, min(days, 14))} dia(s) à frente."
    now = datetime.now().astimezone().strftime("%d/%m %H:%M")
    return f"Agora: {now}. Compromissos:\n" + "\n".join(_event_line(e, True) for e in evs[:40])


async def ver_emails(busca: str = "") -> str:
    try:
        mails = await important_emails(str(busca)[:150], 8)
    except NotConnected:
        return NOT_CONNECTED
    except httpx.HTTPError as e:
        return f"Não consegui ler os e-mails ({e.__class__.__name__})."
    crit = f'busca "{busca}"' if str(busca).strip() else "importantes não lidos das últimas 24 h"
    if not mails:
        return f"Nenhum e-mail encontrado ({crit})."
    lines = [f"- {_day(m['quando'])} {_hm(m['quando'])}, de {m['de']} <{m['email']}>: \"{m['assunto']}\". "
             f"Trecho: {m['trecho']}" for m in mails]
    return f"E-mails ({crit}):\n" + "\n".join(lines)


# web=True: o conteúdo vem de terceiros (convites, e-mails) e é tratado como não confiável
TOOLS = {
    "ver_agenda": {"fn": ver_agenda, "efeito": False, "web": True,
                   "desc": 'lê os compromissos do Google Agenda do usuário (somente leitura), de hoje até N dias. '
                           'args: {"dias": "1"}',
                   "params": {"dias": "quantos dias olhar a partir de hoje: 1 = hoje, 2 = hoje e amanhã, até 14"}},
    "ver_emails": {"fn": ver_emails, "efeito": False, "web": True,
                   "desc": 'lê e-mails do Gmail do usuário (somente leitura). Sem busca, traz os importantes não '
                           'lidos das últimas 24 h; com busca, usa a sintaxe do Gmail (ex.: from:banco newer_than:7d). '
                           'args: {"busca": ""}',
                   "params": {"busca": "vazio para os importantes, ou uma busca no formato do Gmail"}},
}


# ---- conexão (OAuth com PKCE no próprio servidor do Jarvis) ----
router = APIRouter()

PAGE = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Jarvis · Google</title>
<style>body{{font:16px/1.5 -apple-system,system-ui,sans-serif;background:#05090f;color:#cfe9ff;max-width:640px;
margin:40px auto;padding:0 16px}}a,button{{color:#5fd4ff}}code{{color:#ffd27a}}
.box{{border:1px solid #1d4a66;border-radius:10px;padding:16px;margin:16px 0}}</style></head><body>
<h1>Google Agenda e Gmail</h1>{body}<p><a href="/">Voltar ao Jarvis</a></p></body></html>"""


def _page(body: str, code: int = 200) -> HTMLResponse:
    return HTMLResponse(PAGE.format(body=body), status_code=code, headers={"Cache-Control": "no-store"})


@router.get("/google/conectar")
def connect(request: Request):
    try:
        c = _client()
    except NotConnected:
        return _page(f"""<div class="box"><p>Falta a credencial do Google (o arquivo JSON baixado do Google Cloud,
tipo <b>App para computador</b>).</p>
<p><input type="file" id="f" accept=".json,application/json"> <button id="b">Enviar</button></p>
<p id="m"></p><p><small>Ela fica em <code>{html.escape(str(CLIENT_FILE))}</code>, fora do projeto.</small></p></div>
<script>document.getElementById('b').onclick=async()=>{{const f=document.getElementById('f').files[0];
if(!f)return;const r=await fetch('/google/cliente',{{method:'POST',headers:{{'Content-Type':'application/json'}},
body:await f.text()}});if(r.ok)location.reload();else document.getElementById('m').textContent=(await r.json()).detail;}};
</script>""")
    host = request.url.hostname
    if host not in ("127.0.0.1", "localhost", "::1"):
        return _page("<p>Conecte pelo próprio Mac, em <a href='http://127.0.0.1:8000/google/conectar'>"
                     "http://127.0.0.1:8000/google/conectar</a>.</p>", 400)
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(24)
    redirect = str(request.url_for("google_callback"))
    _pending.clear()
    _pending[state] = (verifier, redirect)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    url = httpx.URL(AUTH_URL, params={
        "client_id": c["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    return RedirectResponse(str(url))


@router.post("/google/cliente")
async def upload_client(request: Request):
    try:
        raw = json.loads(await request.body())
    except ValueError:
        raise HTTPException(400, "Isso não é um JSON válido.")
    c = raw.get("installed") if isinstance(raw, dict) else None
    if not c or not c.get("client_id") or not c.get("client_secret"):
        raise HTTPException(400, "Esperava a credencial de 'App para computador' (o JSON começa com \"installed\").")
    _write(CLIENT_FILE, raw)
    return {"ok": True}


@router.get("/google/callback", name="google_callback")
async def callback(state: str = "", code: str = "", error: str = ""):
    if error:
        return _page(f"<p>O Google recusou: <code>{html.escape(error)}</code>. "
                     "<a href='/google/conectar'>Tentar de novo</a>.</p>", 400)
    pend = _pending.pop(state, None)
    if not pend or not code:
        return _page("<p>Sessão de login inválida ou expirada. <a href='/google/conectar'>Tentar de novo</a>.</p>", 400)
    verifier, redirect = pend
    c = _client()
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(TOKEN_URL, data={
            "client_id": c["client_id"], "client_secret": c["client_secret"], "code": code,
            "code_verifier": verifier, "grant_type": "authorization_code", "redirect_uri": redirect})
    if r.status_code != 200:
        return _page(f"<p>Falha ao trocar o código: <code>{html.escape(r.text[:300])}</code></p>", 400)
    d = r.json()
    granted = d.get("scope", "")
    _write(TOKEN_FILE, {"refresh_token": d.get("refresh_token"), "access_token": d["access_token"],
                        "expires_at": time.time() + d.get("expires_in", 3600), "scope": granted})
    _cache.clear()
    missing = [s for s in ("calendar.readonly", "gmail.readonly") if s not in granted]
    warn = (f"<p>Atenção: você não marcou {', '.join(missing)}. Essa parte vai ficar desligada.</p>"
            if missing else "")
    return _page(f"<p>Conectado. O Jarvis agora lê sua agenda e seus e-mails (somente leitura).</p>{warn}")


@router.get("/api/google/status")
def status():
    return {"credencial": CLIENT_FILE.exists(), "conectado": connected(), "pasta": str(DATA_DIR),
            "criterio_emails": GMAIL_QUERY}


@router.post("/google/desconectar")
def disconnect():
    TOKEN_FILE.unlink(missing_ok=True)
    _cache.clear()
    return {"ok": True}
