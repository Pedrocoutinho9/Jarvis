"""Busca na web via Tavily (grátis: 1.000 buscas/mês, sem cartão). Funciona sem biblioteca extra."""
import os
import time

import httpx

_CACHE: dict = {}  # evita gastar créditos com a mesma pergunta repetida


async def search(query: str, n: int = 5) -> str:
    key = os.getenv("TAVILY_API_KEY", "").strip()
    if not key:
        return "Busca na web indisponível: falta a TAVILY_API_KEY no arquivo .env."
    hit = _CACHE.get(query.lower())
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {key}"},
                json={"api_key": key, "query": query, "max_results": n, "search_depth": "basic"},
            )
    except httpx.HTTPError:
        return "A busca na web falhou por erro de rede."
    if r.status_code in (401, 403):
        return "Busca na web recusada: confira a TAVILY_API_KEY."
    if r.status_code >= 400:
        return f"A busca na web falhou (HTTP {r.status_code}); talvez o limite mensal tenha acabado."
    try:
        items = r.json().get("results", [])
    except ValueError:
        items = []
    if not items:
        return "A busca não retornou resultados."
    text = "\n".join(
        f"- {i.get('title', '')}: {(i.get('content') or '')[:350]} ({i.get('url', '')})"
        for i in items[:n]
    )
    _CACHE[query.lower()] = (time.time(), text)
    return text
