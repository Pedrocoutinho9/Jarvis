"""Rotas para ver e editar a memória do Jarvis (página /memoria)."""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import memory

router = APIRouter()
PAGE = Path(__file__).resolve().parent.parent / "frontend" / "memoria.html"


class FactIn(BaseModel):
    fato: str
    categoria: str = "outros"


@router.get("/memoria")
def page():
    return FileResponse(PAGE, headers={"Cache-Control": "no-store"})


@router.get("/api/memoria")
def list_facts():
    return {"fatos": memory.facts(), "categorias": memory.CATEGORIES, "arquivo": str(memory.FILE)}


@router.post("/api/memoria")
def add_fact(body: FactIn):
    if not body.fato.strip():
        raise HTTPException(400, "Fato vazio")
    return memory.add(body.fato, body.categoria)


@router.put("/api/memoria/{fact_id}")
def edit_fact(fact_id: int, body: FactIn):
    item = memory.edit(fact_id, body.fato, body.categoria)
    if not item:
        raise HTTPException(404, "Fato não encontrado")
    return item


@router.delete("/api/memoria/{fact_id}")
def delete_fact(fact_id: int):
    if not memory.remove(fact_id):
        raise HTTPException(404, "Fato não encontrado")
    return {"ok": True}
