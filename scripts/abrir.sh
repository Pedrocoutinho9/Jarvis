#!/bin/zsh
# Abre o Jarvis com dois cliques (chamado pelo ~/Applications/Jarvis.app quando aberto sem argumentos).
# Se não há nada na porta, sobe o servidor da pasta com --reload (código novo vale na hora) e espera ficar no ar.
# Depois traz o HUD para a frente no Safari: reaproveita a aba aberta, ou abre uma nova.
cd "${0:A:h}/.." || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" PYTHONUNBUFFERED=1
PORT="${JARVIS_PORT:-8000}"
URL="http://127.0.0.1:$PORT/"
LOGS="$HOME/Library/Logs/Jarvis"; mkdir -p "$LOGS"
log(){ echo "[jarvis] $(date '+%F %T') $*" >>"$LOGS/jarvis.log"; }

no_ar(){ curl -s -m 2 -o /dev/null "$URL"; }

if ! no_ar && ! lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  if ! curl -s -m 2 http://127.0.0.1:11434/api/version >/dev/null; then
    log "Ollama fora do ar, iniciando"
    if brew services list 2>/dev/null | grep -q '^ollama'; then brew services start ollama >/dev/null 2>&1
    elif [ -d /Applications/Ollama.app ]; then open -ga Ollama
    else nohup ollama serve >>"$LOGS/ollama.log" 2>&1 &
    fi
  fi
  log "abrindo pelo Jarvis.app, subindo o servidor com --reload"
  nohup .venv/bin/uvicorn backend.main:app --reload --host 127.0.0.1 --port "$PORT" >>"$LOGS/jarvis.log" 2>&1 </dev/null &!
  for _ in {1..60}; do no_ar && break; sleep 0.5; done
  no_ar || { osascript -e "display alert \"O Jarvis não subiu\" message \"Veja o log em $LOGS/jarvis.log\""; exit 1; }
fi

# traz a aba do HUD para a frente; se o Safari negar (permissão de Automação), só abre a URL
osascript - "$URL" >/dev/null 2>&1 <<'AS' || open -a Safari "$URL"
on run argv
  set alvo to item 1 of argv
  tell application "Safari"
    activate
    repeat with w in windows
      set i to 0
      repeat with t in tabs of w
        set i to i + 1
        if (URL of t) starts with alvo then
          set current tab of w to t
          set index of w to 1
          return
        end if
      end repeat
    end repeat
    if (count of windows) is 0 then
      make new document with properties {URL:alvo}
      return
    end if
    tell window 1 to set current tab to (make new tab with properties {URL:alvo})
  end tell
end run
AS
