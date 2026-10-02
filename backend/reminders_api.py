"""Rotas dos lembretes: lista, criar/cancelar pelo HUD e o estado que o HUD consulta para falar o alerta."""
import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import reminders

router = APIRouter()
_task = {}


@router.on_event("startup")
async def _start():
    _task["loop"] = asyncio.create_task(reminders.loop())


@router.on_event("shutdown")
async def _stop():
    if _task.get("loop"):
        _task["loop"].cancel()


class ReminderIn(BaseModel):
    texto: str = ""
    quando: str = ""      # lembrete: "18h", "amanhã 9h"...
    duracao: str = ""     # timer: "10 minutos"
    agenda: bool = False  # lembrete: criar também o evento no Google Agenda


@router.get("/api/lembretes")
def list_reminders():
    return {"itens": reminders.pending(), "fuso": reminders.TZ.key, "arquivo": str(reminders.FILE)}


@router.post("/api/lembretes")
async def create(body: ReminderIn):
    out = (await reminders.criar_timer(body.duracao, body.texto) if body.duracao
           else await reminders.criar_lembrete(body.texto, body.quando, "sim" if body.agenda else "nao"))
    if not out.startswith(("Lembrete criado", "Timer criado")):
        raise HTTPException(400, out)
    return {"ok": True, "detalhe": out}


@router.delete("/api/lembretes/{item_id}")
async def delete(item_id: int):
    out = await reminders.cancelar_lembrete(str(item_id))
    if not out.startswith("Cancelado"):
        raise HTTPException(404, "Lembrete não encontrado")
    return {"ok": True, "detalhe": out}


@router.get("/api/lembretes/estado")
def poll(desde: int = -1):
    """Lista pendente e alertas novos desde a última consulta do HUD."""
    return reminders.state(desde)
