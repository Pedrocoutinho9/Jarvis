"""Controle do Spotify (app do Mac, via AppleScript) e do brilho da tela. Sem conta de desenvolvedor.

Para tocar algo específico, o Spotify do Mac só aceita um URI (spotify:track:...). Sem a API oficial,
achamos esse URI buscando no Tavily restrito a open.spotify.com (gasta 1 crédito por pedido).
"""
from __future__ import annotations

import asyncio
import ctypes
import os
import re
import sys
from urllib.parse import quote

import httpx

TIPOS = {"musica": "track", "artista": "artist", "album": "album", "playlist": "playlist"}
_LINK = re.compile(r"open\.spotify\.com/(?:intl-[a-z-]+/)?(track|album|artist|playlist)/([A-Za-z0-9]{22})")
_NO_APP = "O app do Spotify não está instalado neste Mac (baixe em spotify.com/download)."


async def _osa(*lines: str, args: tuple = ()):
    """Roda AppleScript passando valores por argv, nunca colando texto do usuário no script."""
    cmd = ["osascript"]
    for ln in ("on run argv", *lines, "end run"):
        cmd += ["-e", ln]
    try:
        p = await asyncio.create_subprocess_exec(*cmd, *args, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(p.communicate(), timeout=10)
    except Exception as e:
        return 1, str(e)
    return p.returncode, (out if p.returncode == 0 else err).decode(errors="ignore").strip()


async def _spotify_installed() -> bool:
    code, _ = await _osa('return id of application id "com.spotify.client"')
    return code == 0


async def _spotify_running() -> bool:
    code, out = await _osa('return application id "com.spotify.client" is running')
    return code == 0 and out == "true"


async def _now_playing() -> str:
    code, out = await _osa(
        'tell application id "com.spotify.client"',
        'if player state is stopped then return "parado"',
        'set t to current track',
        'return (player state as text) & "|" & (name of t) & "|" & (artist of t) & "|" & (album of t)',
        "end tell")
    if code != 0 or out == "parado":
        return "Nada tocando no Spotify."
    estado, nome, artista, album = (out.split("|") + ["", "", "", ""])[:4]
    verbo = "Tocando" if estado == "playing" else "Pausado em"
    return f"{verbo}: {nome}, de {artista} (álbum {album})."


async def _find_uri(busca: str, tipo: str):
    key = os.getenv("TAVILY_API_KEY", "").strip()
    if not key:
        return None
    nome = {"track": "song", "artist": "artist", "album": "album", "playlist": "playlist"}[tipo]
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post("https://api.tavily.com/search", headers={"Authorization": f"Bearer {key}"},
                                  json={"api_key": key, "query": f"{busca} {nome} spotify", "max_results": 8,
                                        "include_domains": ["open.spotify.com"]})
        res = [(i.get("url", ""), i.get("title", "").lower()) for i in r.json().get("results", [])]
    except (httpx.HTTPError, ValueError):
        return None
    achados = [(m.groups(), t) for m, t in ((_LINK.search(u), t) for u, t in res) if m]
    pref = [a for a in achados if a[0][0] == tipo] or achados
    if not re.search(r"live|ao vivo|remix|cover", busca.lower()):  # versão de estúdio, salvo pedido explícito
        pref.sort(key=lambda a: bool(re.search(r"\blive\b|ao vivo|remix|karaoke|cover", a[1])))
    return f"spotify:{pref[0][0][0]}:{pref[0][0][1]}" if pref else None


# ---- ferramentas ----
async def spotify_tocar(busca: str = "", tipo: str = "musica") -> str:
    if sys.platform != "darwin":
        return "Controle do Spotify só funciona no macOS."
    busca = str(busca or "").strip()[:150]
    tipo = TIPOS.get(str(tipo).lower().replace("ú", "u").replace("á", "a"), "track")
    if not await _spotify_installed():
        if busca:
            uri = await _find_uri(busca, tipo)
            if uri:
                _, kind, sid = uri.split(":")
                await _osa("open location (item 1 of argv)", args=(f"https://open.spotify.com/{kind}/{sid}",))
                return _NO_APP + " Abri no Spotify do navegador; talvez precise apertar play lá."
        return _NO_APP
    if not busca:
        code, out = await _osa('tell application id "com.spotify.client" to play')
        return await _now_playing() if code == 0 else f"Falhou: {out[:150]}"
    uri = await _find_uri(busca, tipo)
    if not uri:
        await _osa("open location (item 1 of argv)", args=("spotify:search:" + quote(busca),))
        return f"Não achei um link exato para '{busca}'; abri a busca no Spotify, falta só escolher."
    code, out = 1, ""
    for _ in range(8):  # se o app acabou de abrir, ele demora uns segundos para aceitar comandos
        code, out = await _osa('tell application id "com.spotify.client" to play track (item 1 of argv)', args=(uri,))
        if code == 0:
            break
        await asyncio.sleep(1)
    if code != 0:
        return f"O Spotify recusou tocar: {out[:150]}"
    for _ in range(6):  # espera a faixa começar de fato antes de dizer o que está tocando
        await asyncio.sleep(0.5)
        agora = await _now_playing()
        if agora.startswith("Tocando"):
            break
    return agora


async def spotify_controle(acao: str) -> str:
    if sys.platform != "darwin":
        return "Controle do Spotify só funciona no macOS."
    a = str(acao).lower().strip()
    cmds = {"pausar": "pause", "continuar": "play", "proxima": "next track", "anterior": "previous track",
            "aleatorio_on": "set shuffling to true", "aleatorio_off": "set shuffling to false"}
    a = {"próxima": "proxima", "pular": "proxima", "voltar": "anterior", "play": "continuar",
         "pause": "pausar", "parar": "pausar", "retomar": "continuar"}.get(a, a)
    if a not in cmds:
        return f"Ação desconhecida: use uma de {', '.join(cmds)}."
    if not await _spotify_installed():
        return _NO_APP
    if not await _spotify_running():
        if a == "pausar":
            return "O Spotify nem está aberto; silêncio garantido."
        if a != "continuar":
            return "O Spotify está fechado. Peça para tocar algo primeiro."
    code, out = await _osa(f'tell application id "com.spotify.client" to {cmds[a]}')
    if code != 0:
        return f"Falhou: {out[:150]}"
    if a == "pausar":
        return "Spotify pausado."
    if a.startswith("aleatorio"):
        return "Aleatório ligado." if a.endswith("on") else "Aleatório desligado."
    await asyncio.sleep(0.8)
    return await _now_playing()


async def spotify_tocando() -> str:
    if sys.platform != "darwin":
        return "Controle do Spotify só funciona no macOS."
    if not await _spotify_installed():
        return _NO_APP
    if not await _spotify_running():
        return "O Spotify está fechado."
    return await _now_playing()


# Brilho: DisplayServices (framework do sistema) ajusta a tela embutida sem sudo nem permissão.
# Monitores externos não respondem a ele (precisariam de DDC, ex.: o utilitário m1ddc).
def _brightness_lib():
    ds = ctypes.CDLL("/System/Library/PrivateFrameworks/DisplayServices.framework/DisplayServices")
    cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    ds.DisplayServicesGetBrightness.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_float)]
    ds.DisplayServicesSetBrightness.argtypes = [ctypes.c_uint32, ctypes.c_float]
    cg.CGDisplayIsBuiltin.restype = ctypes.c_bool
    cg.CGDisplayIsBuiltin.argtypes = [ctypes.c_uint32]
    ids, n = (ctypes.c_uint32 * 8)(), ctypes.c_uint32()
    cg.CGGetOnlineDisplayList(8, ids, ctypes.byref(n))
    return ds, [ids[i] for i in range(n.value)], cg


def _set_brightness(n: int) -> str:
    ds, displays, cg = _brightness_lib()
    ok, externos, f = 0, 0, ctypes.c_float()
    for d in displays:
        if ds.DisplayServicesGetBrightness(d, ctypes.byref(f)) != 0:
            externos += 1
            continue
        if ds.DisplayServicesSetBrightness(d, ctypes.c_float(n / 100)) == 0:
            ok += 1
    if not ok:
        return "Não achei uma tela com brilho ajustável (monitor externo não aceita esse controle)."
    extra = " O monitor externo não aceita controle de brilho por aqui." if externos else ""
    return f"Brilho da tela em {n}%." + extra


async def definir_brilho(nivel) -> str:
    if sys.platform != "darwin":
        return "Controle de brilho só funciona no macOS."
    try:
        n = max(0, min(100, int(float(nivel))))
    except (TypeError, ValueError):
        return "Nível inválido: use um número de 0 a 100."
    try:
        return await asyncio.get_running_loop().run_in_executor(None, _set_brightness, n)
    except OSError:
        return "Este Mac não expõe o controle de brilho."


# efeito=True: só fica disponível com o controle do computador ligado
TOOLS = {
    "spotify_tocar": {"fn": spotify_tocar, "efeito": True, "web": False,
                      "desc": 'toca no Spotify uma música, artista, álbum ou playlist; sem busca, retoma o que estava. '
                              'args: {"busca": "nome e artista", "tipo": "musica|artista|album|playlist"}',
                      "params": {"busca": "o que tocar, ex.: 'Back in Black AC/DC'; vazio retoma",
                                 "tipo": "musica, artista, album ou playlist"}},
    "spotify_controle": {"fn": spotify_controle, "efeito": True, "web": False,
                         "desc": 'controla o Spotify. args: {"acao": "pausar|continuar|proxima|anterior|aleatorio_on|aleatorio_off"}',
                         "params": {"acao": "pausar, continuar, proxima, anterior, aleatorio_on ou aleatorio_off"}},
    "spotify_tocando": {"fn": spotify_tocando, "efeito": True, "web": False,
                        "desc": "diz qual música está tocando no Spotify. args: {}", "params": {}},
    "definir_brilho": {"fn": definir_brilho, "efeito": True, "web": False,
                       "desc": 'define o brilho da tela do Mac. args: {"nivel": 0 a 100}',
                       "params": {"nivel": "número de 0 a 100"}},
}
