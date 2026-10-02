"""Ferramentas que o Jarvis pode acionar (macOS). Nada aqui usa shell: só listas de argumentos."""
import asyncio
import difflib
import html
import ipaddress
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import web

APP_DIRS = [
    Path("/Applications"), Path("/Applications/Utilities"),
    Path("/System/Applications"), Path("/System/Applications/Utilities"),
    Path.home() / "Applications",
]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s).lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]+", " ", s).strip()


# nomes falados -> nome real do app
ALIASES = {_norm(k): v for k, v in {
    "vs code": "Visual Studio Code", "vscode": "Visual Studio Code", "code": "Visual Studio Code",
    "chrome": "Google Chrome", "configurações": "System Settings", "ajustes": "System Settings",
    "preferências do sistema": "System Settings", "calculadora": "Calculator", "notas": "Notes",
    "calendário": "Calendar", "agenda": "Calendar", "música": "Music", "mensagens": "Messages",
    "fotos": "Photos", "relógio": "Clock", "lembretes": "Reminders", "mapas": "Maps",
    "terminal": "Terminal", "whatsapp": "WhatsApp", "zap": "WhatsApp",
}.items()}


def installed_apps() -> list:
    names = []
    for d in APP_DIRS:
        if d.exists():
            names += [p.stem for p in d.glob("*.app")]
    return sorted(set(names))


def resolve_app(name: str, apps=None):
    """Devolve (nome_real, sugestões). Aceita apelidos, acentos e erros leves de pronúncia."""
    apps = installed_apps() if apps is None else apps
    table = {_norm(a): a for a in apps}
    n = _norm(name)
    n = _norm(ALIASES.get(n, name))
    if n in table:
        return table[n], []
    contains = [k for k in table if n and (n in k or k in n)]
    if contains:
        return table[min(contains, key=len)], []
    close = difflib.get_close_matches(n, list(table), n=3, cutoff=0.6)
    if close:
        return table[close[0]], []
    return None, [table[k] for k in difflib.get_close_matches(n, list(table), n=3, cutoff=0.3)]


async def _run(cmd: list):
    try:
        p = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(p.communicate(), timeout=10)
        return p.returncode, out.decode(errors="ignore").strip()
    except Exception as e:
        return 1, str(e)


def _public_url(url: str) -> bool:
    u = urlparse(str(url))
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or not host:
        return False
    if host == "localhost" or host.endswith(".local"):
        return False
    try:
        ip = ipaddress.ip_address(host)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)
    except ValueError:
        return True


# ---- ferramentas ----
async def pesquisar_web(consulta: str) -> str:
    return await web.search(str(consulta)[:200], n=6)


async def ler_pagina(url: str) -> str:
    if not _public_url(url):
        return "URL recusada: só abro endereços http(s) públicos."
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0 (Jarvis)"}) as client:
            r = await client.get(str(url))
        r.raise_for_status()
    except httpx.HTTPError as e:
        return f"Não consegui ler a página ({e.__class__.__name__})."
    raw = r.text[:1_500_000]
    raw = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>", " ", raw)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", raw))
    return re.sub(r"\s+", " ", text).strip()[:4000] or "A página não tem texto legível."


async def abrir_app(nome: str) -> str:
    if sys.platform != "darwin":
        return "Abrir aplicativos só está implementado para macOS por enquanto."
    real, sugestoes = resolve_app(nome)
    if not real:
        extra = f" Parecidos: {', '.join(sugestoes)}." if sugestoes else ""
        return f"Não encontrei nenhum app parecido com '{nome}'.{extra}"
    code, out = await _run(["open", "-a", real])
    return f"{real} aberto." if code == 0 else f"Falhou ao abrir {real}: {out[:150]}"


async def abrir_url(url: str) -> str:
    if not _public_url(url):
        return "URL recusada: só abro endereços http(s) públicos."
    code, out = await _run(["open", str(url)])
    return "Endereço aberto no navegador." if code == 0 else f"Falhou: {out[:150]}"


async def definir_volume(nivel) -> str:
    try:
        n = max(0, min(100, int(float(nivel))))
    except (TypeError, ValueError):
        return "Nível inválido: use um número de 0 a 100."
    code, out = await _run(["osascript", "-e", f"set volume output volume {n}"])
    return f"Volume em {n}%." if code == 0 else f"Falhou: {out[:150]}"


# efeito=True: mexe no computador | web=True: devolve conteúdo externo não confiável
TOOLS = {
    "pesquisar_web": {"fn": pesquisar_web, "efeito": False, "web": True,
                      "desc": 'pesquisa na web e devolve trechos com links. args: {"consulta": "texto curto"}'},
    "ler_pagina": {"fn": ler_pagina, "efeito": False, "web": True,
                   "desc": 'lê o texto de uma página para aprofundar. args: {"url": "https://..."}'},
    "abrir_app": {"fn": abrir_app, "efeito": True, "web": False,
                  "desc": 'abre um aplicativo do Mac. args: {"nome": "Spotify"}'},
    "abrir_url": {"fn": abrir_url, "efeito": True, "web": False,
                  "desc": 'abre um endereço no navegador padrão. args: {"url": "https://..."}'},
    "definir_volume": {"fn": definir_volume, "efeito": True, "web": False,
                       "desc": 'define o volume do Mac. args: {"nivel": 0 a 100}'},
}


def active_tools(web_on: bool, pc_on: bool) -> dict:
    return {n: t for n, t in TOOLS.items() if (t["web"] and web_on) or (t["efeito"] and pc_on)}


async def run_tool(nome: str, args, allowed: dict, tainted: bool, seen_urls: set):
    """Executa uma ferramenta. Devolve (resultado, trouxe_conteudo_externo)."""
    t = allowed.get(nome)
    if not t:
        return f"Ferramenta '{nome}' indisponível.", False
    if not isinstance(args, dict):
        args = {}
    # defesa contra injeção: depois de ler a web, só abre links que apareceram nos resultados
    if tainted and t["efeito"] and not (nome == "abrir_url" and str(args.get("url", "")) in seen_urls):
        return ("Bloqueado por segurança: depois de ler conteúdo da web na mesma pergunta, só abro links "
                "que apareceram nos resultados. Peça a ação diretamente ao usuário."), False
    try:
        out = await t["fn"](**args)
    except TypeError:
        out = "Argumentos inválidos para essa ferramenta."
    except Exception as e:
        out = f"Erro ao executar: {e.__class__.__name__}"
    return str(out)[:6000], t["web"]
