import html
import os
from pathlib import Path

import edge_tts
import feedparser
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

load_dotenv()

CITY = os.getenv("CITY_NAME", "Recife")
LAT = float(os.getenv("LAT", "-8.05"))
LON = float(os.getenv("LON", "-34.88"))

VOICE = os.getenv("TTS_VOICE", "pt-BR-AntonioNeural")

FEEDS = {
    "Tecnologia": "https://g1.globo.com/rss/g1/tecnologia/",
    "Política": "https://g1.globo.com/rss/g1/politica/",
}

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
app = FastAPI(title="Jarvis")


async def weather_card() -> dict:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "current": "temperature_2m,apparent_temperature,wind_speed_10m",
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
        return {"id": "clima", "k": "Clima", "t": CITY,
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
            "k": "Notícias",
            "t": topic,
            "html": f"<ul>{lis}</ul>",
            "say": f"Nas notícias de {topic.lower()}: {items[0].title}.",
        }
    except Exception:
        return {"id": f"news-{topic.lower()}", "k": "Notícias", "t": topic,
                "html": "<p>Feed indisponível no momento.</p>",
                "say": f"Não consegui carregar as notícias de {topic.lower()}."}


def agenda_card() -> dict:  # placeholder até o passo do Google OAuth
    return {"id": "agenda", "k": "Hoje", "t": "Agenda livre",
            "html": "<p>Nenhum compromisso registrado.</p>",
            "say": "A agenda de hoje está livre, senhor."}


def email_card() -> dict:  # placeholder até o passo do Gmail
    return {"id": "email", "k": "E-mails", "t": "Pedem sua atenção",
            "html": "<p>Dados de exemplo. Conexão com o Gmail vem depois.</p>",
            "say": "A leitura de e-mails ainda não foi conectada."}


@app.get("/api/briefing")
async def briefing():
    cards = [agenda_card(), email_card(), await weather_card()]
    for topic, url in FEEDS.items():
        cards.append(await news_card(topic, url))
    return cards


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