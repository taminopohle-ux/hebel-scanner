# Hebel-Scanner

Zeigt für ~40 Aktien (US + DAX) historische Wahrscheinlichkeiten für 3- und 5-Tage-Trades
mit Knock-out-Hebel 2x/5x/10x, Long und Short. **Keine Empfehlung, keine Prognose.**

- `dashboard.html`: im Browser öffnen (Doppelklick)
- `scanner.py`: lädt Daten und erzeugt das Dashboard neu (`.venv/bin/python scanner.py`)
- `config.json`: Aktienliste, Haltedauern, Hebel anpassen (Yahoo-Ticker, z. B. `RHM.DE`)
- `install_daily.sh`: tägliche Aktualisierung Mo–Fr 07:30 einrichten (`--uninstall` zum Entfernen)
- `data/`: Archiv aller Tages-Scans als JSON

Datenquellen: Yahoo Finance (Kurse, Zahlen-Termine), Google News RSS (Schlagzeilen).

## Webseite (GitHub Pages)

`.github/workflows/daily.yml` erzeugt das Dashboard Mo–Fr automatisch bei GitHub und
veröffentlicht es auf GitHub Pages. Manuell starten: im Repo unter *Actions* →
*Dashboard täglich aktualisieren* → *Run workflow*.
