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

## Google Agenda e Gmail (somente leitura)

1. Em https://console.cloud.google.com crie um projeto (ex.: Jarvis).
2. Em *APIs e serviços > Biblioteca*, ative **Google Calendar API** e **Gmail API**.
3. Em *Google Auth Platform* (tela de consentimento): tipo **Externo**, preencha nome e e-mail e, em *Público-alvo*,
   adicione seu Gmail como usuário de teste.
4. Em *Clientes*, crie um ID do cliente OAuth do tipo **App para computador** e baixe o JSON.
5. Com o Jarvis rodando, abra http://127.0.0.1:8000/google/conectar, envie o JSON e autorize.

Credencial e token ficam em `~/Library/Application Support/Jarvis` (fora do repositório). Escopos:
`calendar.readonly`, `gmail.readonly` e `calendar.events`. E-mails são só leitura; na agenda, o Jarvis só cria
um evento quando você pede para pôr um lembrete na agenda (e o apaga se o lembrete for cancelado).
Quem conectou antes dessa permissão precisa reconectar uma vez em /google/conectar.

## Lembretes e timers

"Me lembra de X às 18h", "timer de 10 minutos", "quais lembretes eu tenho", "cancela o do X". Na hora, o HUD fala
o aviso e o macOS mostra uma notificação (desligue com `REMINDER_NOTIFY=off`). Ficam em
`~/Library/Application Support/Jarvis/lembretes.json` e sobrevivem a reinícios. Fuso em `JARVIS_TZ`
(padrão `America/Recife`).
"E-mails importantes" = não lidos, últimas 24 h, marcados como Importantes pelo Gmail, fora de Promoções e Social
(mude com `GMAIL_QUERY` no `.env`). Com o app em modo *Teste*, o Google derruba o acesso a cada 7 dias; para não
reconectar toda semana, clique em *Publicar app* no Público-alvo (o Google mostra um aviso de app não verificado
no login, é só seguir em *Avançado*).

## Roadmap
- [x] Interface com orbe e cards
- [x] Backend FastAPI, clima (Open-Meteo) e notícias (RSS)
- [x] Voz: fala (Edge TTS) e escuta (reconhecimento do navegador)
- [x] Conversa por voz com LLM (Gemini grátis ou Ollama local)
- [x] Gmail e Google Agenda (somente leitura)
- [ ] LLM para priorizar o briefing