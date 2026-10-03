#!/bin/zsh
# Liga/desliga o Jarvis no login do Mac.
#   scripts/autostart.sh ligar      instala e carrega o LaunchAgent (sobe o servidor agora e em todo login)
#   scripts/autostart.sh desligar   descarrega e remove o LaunchAgent
#   scripts/autostart.sh app        só (re)cria o ~/Applications/Jarvis.app (dois cliques: sobe o servidor e abre o HUD)
#   scripts/autostart.sh status     mostra se está carregado e as últimas linhas do log
# Para manter o servidor no login mas sem o briefing automático: AUTO_BRIEFING=off no .env.
LABEL="local.jarvis"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
ROOT="${0:A:h:h}"
LOGS="$HOME/Library/Logs/Jarvis"
DOMAIN="gui/$(id -u)"
APP="$HOME/Applications/Jarvis.app"   # lançador nativo: só um app consegue pedir acesso à pasta Mesa

build_app(){
  mkdir -p "$APP/Contents/MacOS"
  clang -O2 -DJARVIS_ABRIR="\"$ROOT/scripts/abrir.sh\"" -o "$APP/Contents/MacOS/Jarvis" "$ROOT/scripts/launcher.c" || return 1
  cat > "$APP/Contents/Info.plist" <<IP
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>$LABEL</string>
  <key>CFBundleName</key><string>Jarvis</string>
  <key>CFBundleExecutable</key><string>Jarvis</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSUIElement</key><true/>
  <key>NSDesktopFolderUsageDescription</key><string>O Jarvis roda a partir da pasta Mesa.</string>
</dict></plist>
IP
  codesign --force --sign - "$APP" >/dev/null 2>&1
}

case "$1" in
  ligar|on)
    mkdir -p "$LOGS" "$HOME/Library/LaunchAgents"
    chmod +x "$ROOT/scripts/jarvis-launchd.sh"
    build_app || { echo "Falhou ao compilar o lançador (precisa do clang: xcode-select --install)"; exit 1; }
    cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$APP/Contents/MacOS/Jarvis</string><string>$ROOT/scripts/jarvis-launchd.sh</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$LOGS/jarvis.log</string>
  <key>StandardErrorPath</key><string>$LOGS/jarvis.log</string>
</dict>
</plist>
PL
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null
    launchctl bootstrap "$DOMAIN" "$PLIST" && echo "Jarvis ligado no login ($PLIST). Log: $LOGS/jarvis.log"
    ;;
  desligar|off)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null
    rm -f "$PLIST"; echo "Jarvis não sobe mais no login (o $APP continua, para abrir com dois cliques)."
    ;;
  app)
    chmod +x "$ROOT/scripts/abrir.sh"
    build_app || { echo "Falhou ao compilar o lançador (precisa do clang: xcode-select --install)"; exit 1; }
    echo "Pronto: $APP (dois cliques sobem o servidor, se preciso, e abrem o HUD no Safari)"
    ;;
  status)
    launchctl print "$DOMAIN/$LABEL" 2>/dev/null | grep -E "state|pid|last exit" || echo "LaunchAgent não carregado."
    tail -n 15 "$LOGS/jarvis.log" 2>/dev/null
    ;;
  *) echo "uso: $0 ligar | desligar | app | status"; exit 1 ;;
esac
