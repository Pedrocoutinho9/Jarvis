#!/bin/zsh
# Sobe o Jarvis no login (chamado pelo LaunchAgent criado por scripts/autostart.sh).
# Garante o Ollama no ar e inicia o servidor sem --reload. Logs em ~/Library/Logs/Jarvis.
cd "${0:A:h}/.." || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export JARVIS_LAUNCHD=1 PYTHONUNBUFFERED=1
PORT="${JARVIS_PORT:-8000}"
echo "[jarvis] $(date '+%F %T') iniciando pelo LaunchAgent"

if ! curl -s -m 2 http://127.0.0.1:11434/api/version >/dev/null; then
  echo "[jarvis] Ollama fora do ar, iniciando"
  if brew services list 2>/dev/null | grep -q '^ollama'; then brew services start ollama
  elif [ -d /Applications/Ollama.app ]; then open -ga Ollama
  else nohup ollama serve >>"$HOME/Library/Logs/Jarvis/ollama.log" 2>&1 &
  fi
fi

# se já tem um Jarvis rodando nessa porta (ex.: o uvicorn --reload aberto à mão), não sobe outro
if curl -s -m 2 "http://127.0.0.1:$PORT/api/auto-briefing/pendente" >/dev/null \
   || lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "[jarvis] porta $PORT já em uso, não vou subir outro servidor"
  exit 0
fi
exec .venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port "$PORT"
