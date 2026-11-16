#!/bin/zsh
# Richtet die tägliche Aktualisierung ein (Mo–Fr um 07:30, vor Börsenstart).
# Läuft der Mac um 07:30 nicht, holt macOS den Lauf beim nächsten Aufwachen nach.
# Entfernen:  ./install_daily.sh --uninstall
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="local.hebel-scanner"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ "$1" == "--uninstall" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"; echo "Tägliche Aktualisierung entfernt."; exit 0
fi

if ! "$DIR/.venv/bin/python" -c "import yfinance, pandas, numpy" 2>/dev/null; then
  echo "Richte Python-Umgebung ein …"
  rm -rf "$DIR/.venv"; python3 -m venv "$DIR/.venv"
  "$DIR/.venv/bin/python" -m pip install -q yfinance pandas numpy
fi

mkdir -p "$HOME/Library/LaunchAgents"
DAYS=""
for d in 1 2 3 4 5; do
  DAYS+="<dict><key>Weekday</key><integer>$d</integer><key>Hour</key><integer>7</integer><key>Minute</key><integer>30</integer></dict>"
done
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$DIR/.venv/bin/python</string><string>$DIR/scanner.py</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>StartCalendarInterval</key><array>$DAYS</array>
  <key>StandardOutPath</key><string>$DIR/scanner.log</string>
  <key>StandardErrorPath</key><string>$DIR/scanner.log</string>
</dict></plist>
EOF
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Eingerichtet: $DIR/dashboard.html wird Mo–Fr um 07:30 neu erzeugt."
