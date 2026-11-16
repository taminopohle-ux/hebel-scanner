#!/usr/bin/env python3
"""
Schickt morgens eine Push-Nachricht über ntfy (https://ntfy.sh), wenn es etwas zu tun gibt:
  - neue Kauf-Signale aus dem heutigen Scan
  - Verkaufs-Erinnerungen für Signale der letzten Tage (Haltedauer vorbei,
    Signal gedreht, Quartalszahlen vor dem Ausstieg)

Gleiche Regeln wie die Webseite. Ohne Umgebungsvariable NTFY_TOPIC passiert nichts.
Aufruf:  python notify.py            (echte Nachricht, nur wenn es etwas gibt)
         python notify.py --dry-run  (nur anzeigen)
         python notify.py --test     (Testnachricht senden)
"""
import json
import os
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text())
R = CFG["signal_rules"]
L = str(R["leverage"])
PAGE = CFG.get("page_url", "")


def add_trading_days(ds, n):
    d = date.fromisoformat(ds)
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d.isoformat()


def signal(row, H, ref_date):
    h = row["h"][H]
    edge = h["p_up"] - row["base"][H]["p_up"]
    direction = "long" if edge >= R["edge"] else "short" if edge <= -R["edge"] else None
    if not direction or row["n"] < R["minN"]:
        return None, edge
    e = row.get("earnings")
    if e:
        days = (date.fromisoformat(e) - date.fromisoformat(ref_date)).days
        if 0 <= days <= int(H) + 2:
            return None, edge
    if h["lev"][L][direction]["ko"] > R["maxKO"]:
        return None, edge
    return direction, edge


def build_message(today):
    scans = sorted((ROOT / "data").glob("scan_*.json"))
    if not scans:
        return None
    latest = json.loads(scans[-1].read_text())
    H = str(latest["horizons"][0])
    rows = {r["ticker"]: r for r in latest["rows"]}
    exit_new = add_trading_days(latest["data_date"], int(H))

    buys, sells = [], []
    for r in latest["rows"]:
        d, _ = signal(r, H, latest["data_date"])
        if d:
            p = r["h"][H]["p_up"] if d == "long" else 1 - r["h"][H]["p_up"]
            buys.append(f"{'▲' if d == 'long' else '▼'} {r['name']} {('Long (Call)' if d == 'long' else 'Short (Put)')}"
                        f" · {round(p * 100)}/100 Treffer · verkaufen bis {date.fromisoformat(exit_new):%d.%m.}")

    # Verkaufs-Erinnerungen aus früheren Scans
    seen = set()
    for path in scans[-12:-1]:
        old = json.loads(path.read_text())
        exit_d = add_trading_days(old["data_date"], int(H))
        if exit_d < today:
            continue
        for r in old["rows"]:
            d, _ = signal(r, H, old["data_date"])
            if not d or r["ticker"] in seen:
                continue
            seen.add(r["ticker"])
            now = rows.get(r["ticker"])
            when = f"Signal vom {date.fromisoformat(old['data_date']):%d.%m.}"
            reason = None
            if exit_d == today:
                reason = "Haltedauer endet heute"
            elif now:
                _, edge_now = signal(now, H, latest["data_date"])
                if (d == "long" and edge_now <= -R["edge"]) or (d == "short" and edge_now >= R["edge"]):
                    reason = "Signal hat gedreht"
                e = now.get("earnings")
                if not reason and e and today <= e <= exit_d:
                    reason = f"Quartalszahlen am {date.fromisoformat(e):%d.%m.}"
            if reason:
                sells.append(f"🔔 {r['name']} {'Long' if d == 'long' else 'Short'} ({when}): jetzt verkaufen, {reason}")

    if not buys and not sells:
        return None
    parts = []
    if sells:
        parts.append("Falls du gekauft hast:\n" + "\n".join(sells))
    if buys:
        parts.append("Neue Signale:\n" + "\n".join(buys))
    title = ("Verkaufen + " if sells and buys else "Verkaufen: " if sells else "") + \
            (f"{len(buys)} Kauf-Signal{'e' if len(buys) != 1 else ''}" if buys else f"{len(sells)} Trade{'s' if len(sells) != 1 else ''}")
    return title, "\n\n".join(parts) + "\n\nKeine Anlageberatung."


def send(title, body, tags="chart_with_upwards_trend"):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("NTFY_TOPIC nicht gesetzt, keine Nachricht gesendet.")
        return
    payload = {"topic": topic, "title": title, "message": body, "tags": [tags], "priority": 4}
    if PAGE:
        payload["click"] = PAGE
    req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        print("Gesendet:", resp.status)


def main():
    today = datetime.now(ZoneInfo("Europe/Berlin")).date().isoformat()
    if "--test" in sys.argv:
        send("Hebel-Scanner verbunden", "Ab jetzt kommt hier morgens eine Nachricht, wenn es ein Kauf- oder Verkaufs-Signal gibt.", "white_check_mark")
        return
    msg = build_message(today)
    if not msg:
        print("Heute nichts zu melden.")
        return
    print(msg[0] + "\n" + msg[1])
    if "--dry-run" not in sys.argv:
        send(*msg)


if __name__ == "__main__":
    main()
