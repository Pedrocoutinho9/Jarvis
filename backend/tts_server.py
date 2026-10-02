"""Servidor de voz local (Chatterbox), separado do Jarvis porque precisa do Python 3.11.

Rodar:  .venv-tts/bin/python -m backend.tts_server
O Jarvis chama GET http://127.0.0.1:8765/tts?text=... quando TTS_ENGINE=chatterbox.
"""
import asyncio
import io
import os
import time
from pathlib import Path

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import torch
import torchaudio
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

REF = ROOT / os.getenv("CHATTERBOX_REF", "voices/andrew_ref.mp3")
EXAGGERATION = float(os.getenv("CHATTERBOX_EXAGGERATION", "0.5"))
CFG = float(os.getenv("CHATTERBOX_CFG", "0.5"))
DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"

app = FastAPI()
model = None
lock = asyncio.Lock()  # uma fala por vez: a GPU não ganha nada gerando duas juntas


@app.on_event("startup")
def load():
    global model
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS
    t = time.time()
    model = ChatterboxMultilingualTTS.from_pretrained(device=DEVICE)
    model.prepare_conditionals(str(REF), exaggeration=EXAGGERATION)  # timbre calculado uma vez só
    print(f"[voz] Chatterbox pronto em {time.time() - t:.0f}s ({DEVICE}, referência {REF.name})")


def _wav(text: str) -> bytes:
    t = time.time()
    with torch.inference_mode():
        audio = model.generate(text, language_id="pt", exaggeration=EXAGGERATION, cfg_weight=CFG)
    out = io.BytesIO()
    torchaudio.save(out, audio.cpu(), model.sr, format="wav")
    print(f"[voz] {time.time() - t:.1f}s para {audio.shape[-1] / model.sr:.1f}s de fala: {text[:50]!r}")
    return out.getvalue()


@app.get("/tts")
async def tts(text: str = Query(..., max_length=1500)):
    if model is None:
        raise HTTPException(503, "Modelo de voz ainda carregando")
    async with lock:
        return Response(await run_in_threadpool(_wav, text), media_type="audio/wav")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("TTS_SERVER_PORT", "8765")))
