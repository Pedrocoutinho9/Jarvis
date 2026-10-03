"""Briefing automático: de manhã, quando o Mac liga ou acorda e o usuário está na frente dele, o Jarvis abre o
HUD no Safari e fala o briefing sozinho, uma vez por dia.

Como funciona: um laço no servidor checa a cada 20 s se está na janela da manhã, se o briefing de hoje ainda não
saiu, se a tela está desbloqueada e se alguém mexeu no teclado ou mouse há pouco. Se sim, e nenhum HUD aberto
perguntou nada recentemente, abre http://127.0.0.1:8000/?auto=1 no Safari. O HUD pergunta em
/api/auto-briefing/pendente, roda o briefing e avisa em /api/auto-briefing/feito.

Áudio: o Safari bloqueia som sem um clique. Quando isso acontece o HUD manda cada fala para /api/falar-local,
que toca o MP3 pelo afplay do macOS (o HUD continua animando com o áudio mudo, que o Safari permite).

.env: AUTO_BRIEFING=off desliga; AUTO_BRIEFING_INICIO/FIM = janela em horas (padrão 5 e 12); fuso em JARVIS_TZ.
O LaunchAgent (scripts/autostart.sh) define JARVIS_LAUNCHD=1: aí o Safari abre logo após o login, sem esperar.
"""
import asyncio
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from .reminders import TZ

router = APIRouter()
DATA_DIR = Path(os.getenv("JARVIS_DATA_DIR") or Path.home() / "Library/Application Support/Jarvis").expanduser()
FILE = DATA_DIR / "briefing_auto.json"
PORT = os.getenv("JARVIS_PORT", "8000")
LAUNCHD = os.getenv("JARVIS_LAUNCHD") == "1"
CLAIM_S = 600        # um HUD pegou o briefing: os outros esperam 10 min antes de tentar de novo
HUD_SEEN_S = 75      # um HUD que perguntou há menos que isso dá conta sozinho, não precisa abrir o Safari
IDLE_MAX_S = 300     # só fala se alguém usou teclado/mouse nos últimos 5 min
OPEN_GAP_S = 900     # não abre o Safari de novo antes de 15 min

_state = {"hud_seen": 0.0, "opened": 0.0, "pending_since": 0.0, "claimed": 0.0}
_task: dict = {}


def enabled() -> bool:
    return os.getenv("AUTO_BRIEFING", "on").strip().lower() not in ("off", "0", "false", "nao", "não")


def _window():
    return int(os.getenv("AUTO_BRIEFING_INICIO", "5")), int(os.getenv("AUTO_BRIEFING_FIM", "12"))


def _today() -> str:
    return datetime.now(TZ).date().isoformat()


def done_today() -> bool:
    try:
        return json.loads(FILE.read_text()).get("feito") == _today()
    except (OSError, ValueError):
        return False


def _mark_done():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps({"feito": _today(), "hora": datetime.now(TZ).isoformat(timespec="seconds")}))


def _ioreg(*args) -> str:
    try:
        return subprocess.run(["/usr/sbin/ioreg", *args], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""


def user_present() -> bool:
    """Tela desbloqueada e alguém usou teclado ou mouse há pouco."""
    if "CGSSessionScreenIsLocked" in _ioreg("-n", "Root", "-d1"):
        return False
    for line in _ioreg("-c", "IOHIDSystem", "-d4").splitlines():
        if '"HIDIdleTime"' in line:
            try:
                return int(line.split("=")[-1]) / 1e9 < IDLE_MAX_S
            except ValueError:
                break
    return True


def due() -> bool:
    start, end = _window()
    return enabled() and start <= datetime.now(TZ).hour < end and not done_today()


async def loop():
    started = time.time()
    while True:
        try:
            now = time.time()
            if due() and await asyncio.to_thread(user_present):
                _state["pending_since"] = _state["pending_since"] or now
                fresh_login = LAUNCHD and now - started < 120   # acabou de logar: abre já
                waited = now - _state["pending_since"] > HUD_SEEN_S  # acordou do repouso: dá tempo ao HUD aberto
                if ((fresh_login or waited) and now - _state["hud_seen"] > HUD_SEEN_S
                        and now - _state["opened"] > OPEN_GAP_S and now - _state["claimed"] > CLAIM_S):
                    _state["opened"] = now
                    print("[jarvis] briefing automático: abrindo o HUD no Safari", flush=True)
                    subprocess.Popen(["/usr/bin/open", "-a", "Safari", f"http://127.0.0.1:{PORT}/?auto=1"])
            else:
                _state["pending_since"] = 0.0
        except Exception as e:
            print(f"[jarvis] briefing automático: {type(e).__name__}: {e}", flush=True)
        await asyncio.sleep(20)


@router.on_event("startup")
async def _start():
    _task["loop"] = asyncio.create_task(loop())


@router.on_event("shutdown")
async def _stop():
    if "loop" in _task:
        _task["loop"].cancel()


@router.get("/api/auto-briefing/pendente")
async def pendente():
    """O HUD pergunta ao abrir e a cada minuto. Só um HUD por vez leva o briefing."""
    _state["hud_seen"] = time.time()
    run = due() and time.time() - _state["claimed"] > CLAIM_S and await asyncio.to_thread(user_present)
    if run:
        _state["claimed"] = time.time()
    return {"rodar": run}


@router.post("/api/auto-briefing/feito")
async def feito():
    _mark_done()
    _state["claimed"] = 0.0
    return {"ok": True}


class Fala(BaseModel):
    text: str


_speak_lock = asyncio.Lock()


@router.post("/api/falar-local")
async def falar_local(f: Fala):
    """Toca a fala pelos alto-falantes do Mac (afplay), para quando o Safari bloqueia o som sem clique."""
    from . import main  # importado aqui para não criar ciclo
    resp = await main.tts(f.text[:1500])
    suffix = ".wav" if resp.media_type == "audio/wav" else ".mp3"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(resp.body)
    try:
        async with _speak_lock:
            proc = await asyncio.create_subprocess_exec("/usr/bin/afplay", tmp.name)
            await proc.wait()
    finally:
        os.unlink(tmp.name)
    return {"ok": True}
