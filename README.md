# Jarvis

Assistente pessoal com briefing matinal, interface em estilo HUD e voz.

## Como rodar

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # ajuste cidade, coordenadas e GEMINI_API_KEY
uvicorn backend.main:app --reload
```

Abra http://127.0.0.1:8000

## Roadmap
- [x] Interface com orbe e cards
- [x] Backend FastAPI, clima (Open-Meteo) e notícias (RSS)
- [x] Voz: fala (Edge TTS) e escuta (reconhecimento do navegador)
- [x] Conversa por voz com LLM (Gemini grátis ou Ollama local)
- [ ] Gmail e Google Agenda (somente leitura)
- [ ] LLM para priorizar o briefing