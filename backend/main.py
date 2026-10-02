import html
import os
import re
import time
from datetime import datetime
from pathlib import Path

import edge_tts
import feedparser
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import llm

load_dotenv()

CITY = os.getenv("CITY_NAME", "Recife")
LAT = float(os.getenv("LAT", "-8.05"))
LON = float(os.getenv("LON", "-34.88"))

VOICE = os.getenv("TTS_VOICE", "pt-BR-AntonioNeural")
USER_TITLE = os.getenv("USER_TITLE", "senhor")
BRIEFING_CACHE: list = []  # último briefing, usado como contexto na conversa

FEEDS = {
    "Tecnologia": "https://g1.globo.com/rss/g1/tecnologia/",
    "Política": "https://g1.globo.com/rss/g1/politica/",
}

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
app = FastAPI(title="Jarvis")


def _wicon(code: int) -> str:
    if code in (0, 1):
        return "sun"
    if code in (2, 3, 45, 48):
        return "cloud"
    return "rain"


def _image(entry):
    for key in ("media_content", "media_thumbnail"):
        for m in entry.get(key) or []:
            if str(m.get("url", "")).startswith("http"):
                return m["url"]
    for link in entry.get("links", []):
        if link.get("type", "").startswith("image") and link.get("href", "").startswith("http"):
            return link["href"]
    m = re.search(r'<img[^>]+src="(https?://[^"]+)"', entry.get("summary", "") or "")
    return m.group(1) if m else None


_MARKET = {"t": 0.0, "data": None}
MARKET_URL = "https://economia.awesomeapi.com.br/json/last/USD-BRL,EUR-BRL,BTC-BRL"


async def market_data():
    """Cotações em reais (cache de 60 s). Devolve None se a API estiver fora do ar."""
    if _MARKET["data"] and time.time() - _MARKET["t"] < 60:
        return _MARKET["data"]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(MARKET_URL)
            r.raise_for_status()
            d = r.json()
        out = {}
        for key, name in (("USDBRL", "Dólar"), ("EURBRL", "Euro"), ("BTCBRL", "Bitcoin")):
            if key in d:
                out[name] = {"bid": float(d[key]["bid"]), "pct": float(d[key]["pctChange"])}
        if out:
            _MARKET.update(t=time.time(), data=out)
            return out
    except Exception:
        pass
    return _MARKET["data"]  # último valor conhecido (ou None)


def _br(x: float, nd: int = 2) -> str:
    s = f"{x:,.{nd}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


async def market_card() -> dict:
    m = await market_data()
    if not m:
        return {"id": "mercado", "icon": "chart", "k": "Mercado", "t": "Câmbio",
                "html": "<p>Cotações indisponíveis no momento.</p>",
                "say": "Não consegui obter as cotações agora."}
    lis = "".join(
        f"<li>{n}: R$ {_br(v['bid'], 0 if n == 'Bitcoin' else 2)} ({_br(v['pct'])}%)</li>"
        for n, v in m.items()
    )
    say = []
    if "Dólar" in m:
        d = m["Dólar"]
        mov = "em alta" if d["pct"] >= 0 else "em queda"
        say.append(f"O dólar está a {_br(d['bid'])} reais, {mov} de {_br(abs(d['pct']))} por cento.")
    if "Euro" in m:
        say.append(f"O euro, a {_br(m['Euro']['bid'])} reais.")
    return {"id": "mercado", "icon": "chart", "k": "Mercado", "t": "Câmbio",
            "html": f"<ul>{lis}</ul>", "say": " ".join(say) or "As cotações estão na tela."}


async def weather_card() -> dict:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "current": "temperature_2m,apparent_temperature,wind_speed_10m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": "auto",
        "forecast_days": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            d = r.json()
        now, day = d["current"], d["daily"]
        tmin = round(day["temperature_2m_min"][0])
        tmax = round(day["temperature_2m_max"][0])
        rain = day["precipitation_probability_max"][0]
        temp = round(now["temperature_2m"])
        return {
            "id": "clima",
            "icon": _wicon(now["weather_code"]),
            "k": "Clima",
            "t": CITY,
            "html": (
                f'<p class="big">{temp}°</p>'
                f"<p>Mín {tmin}° · Máx {tmax}° · chuva {rain}%</p>"
                f'<p><small>Sensação {round(now["apparent_temperature"])}° · '
                f'vento {now["wind_speed_10m"]} km/h</small></p>'
            ),
            "say": f"Em {CITY}, agora fazem {temp} graus. A máxima será de {tmax} e a chance de chuva é de {rain} por cento.",
        }
    except Exception as e:
        return {"id": "clima", "icon": "cloud", "k": "Clima", "t": CITY,
                "html": "<p>Não foi possível obter o clima agora.</p>",
                "say": "Não consegui obter a previsão do tempo."}


async def news_card(topic: str, url: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
        feed = feedparser.parse(r.text)
        items = feed.entries[:3]
        if not items:
            raise ValueError("feed vazio")
        lis = "".join(f"<li>{html.escape(i.title)}</li>" for i in items)
        return {
            "id": f"news-{topic.lower()}",
            "icon": "news",
            "image": _image(items[0]),
            "k": "Notícias",
            "t": topic,
            "html": f"<ul>{lis}</ul>",
            "say": f"Nas notícias de {topic.lower()}: {items[0].title}.",
        }
    except Exception:
        return {"id": f"news-{topic.lower()}", "icon": "news", "k": "Notícias", "t": topic,
                "html": "<p>Feed indisponível no momento.</p>",
                "say": f"Não consegui carregar as notícias de {topic.lower()}."}


def agenda_card() -> dict:  # placeholder até o passo do Google OAuth
    return {"id": "agenda", "icon": "calendar", "k": "Hoje", "t": "Agenda livre",
            "html": "<p>Nenhum compromisso registrado.</p>",
            "say": "A agenda de hoje está livre, senhor."}


def email_card() -> dict:  # placeholder até o passo do Gmail
    return {"id": "email", "icon": "mail", "k": "E-mails", "t": "Pedem sua atenção",
            "html": "<p>Dados de exemplo. Conexão com o Gmail vem depois.</p>",
            "say": "A leitura de e-mails ainda não foi conectada."}


@app.get("/api/briefing")
async def briefing():
    cards = [agenda_card(), email_card(), await weather_card(), await market_card()]
    for topic, url in FEEDS.items():
        cards.append(await news_card(topic, url))
    BRIEFING_CACHE[:] = cards
    return cards


def _plain(markup: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", markup))
    return re.sub(r"\s+", " ", text).strip()


def system_prompt(market=None) -> str:
    lines = [f"- {c['k']} ({c['t']}): {_plain(c['html'])}" for c in BRIEFING_CACHE if c["id"] != "mercado"]
    if market:
        quotes = "; ".join(f"{n} R$ {_br(v['bid'], 0 if n == 'Bitcoin' else 2)} ({_br(v['pct'])}% no dia)" for n, v in market.items())
        lines.append(f"- Cotações em tempo real: {quotes}")
    data = "\n".join(lines) if lines else "Nenhum briefing foi carregado ainda."
    return (
        f'Você é o Jarvis, assistente pessoal por voz. Trate o usuário por "{USER_TITLE}". '
        "Responda em português do Brasil, de forma natural e direta, em no máximo 3 frases curtas, "
        "porque sua resposta será lida em voz alta: nada de markdown, listas, emojis ou símbolos. "
        "Se não souber algo, diga com franqueza e não invente dados. "
        f"Cidade do usuário: {CITY}. Data de hoje: {datetime.now().strftime('%d/%m/%Y')}.\n\n"
        "Dados do briefing de hoje (use quando a pergunta for sobre eles):\n" + data
    )


class Msg(BaseModel):
    role: str
    content: str


class ChatIn(BaseModel):
    messages: list[Msg]


def _trim(text: str, limit: int = 580) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: end + 1] if end > 100 else cut


@app.post("/api/chat")
async def chat(body: ChatIn):
    msgs = [{"role": "assistant" if m.role == "assistant" else "user", "content": m.content[:1000]}
            for m in body.messages[-12:]]
    if not msgs or msgs[-1]["role"] != "user":
        raise HTTPException(400, "Mensagem inválida")
    try:
        reply = await llm.ask(msgs, system_prompt(await market_data()))
    except llm.LLMError as e:
        raise HTTPException(503, str(e))
    except httpx.HTTPError:
        raise HTTPException(502, "Falha de rede ao consultar o modelo.")
    return {"reply": _trim(reply)}


@app.get("/api/tts")
async def tts(text: str = Query(..., max_length=600)):
    audio = b""
    try:
        async for chunk in edge_tts.Communicate(text, VOICE).stream():
            if chunk["type"] == "audio":
                audio += chunk["data"]
    except Exception:
        raise HTTPException(502, "Falha ao gerar áudio")
    if not audio:
        raise HTTPException(502, "Áudio vazio")
    return Response(audio, media_type="audio/mpeg")


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")