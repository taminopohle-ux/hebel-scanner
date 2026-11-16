#!/usr/bin/env python3
"""
Hebel-Scanner: berechnet für eine Aktienliste historische Wahrscheinlichkeiten
für kurzfristige (3/5 Tage) Bewegungen und Knock-out-Risiken bei Hebel 2/5/10.

KEINE Anlageberatung. Die Zahlen sind historische Basisraten aus ähnlichen
Marktsituationen, keine Prognose.

Methode (kurz):
  1. 3 Jahre Tagesdaten aller Aktien laden.
  2. Jeden Tag in ein "Setup" einordnen: Trend (SMA20/50), RSI-Zone, Vorfall
     (Tagesbewegung > 2 Standardabweichungen).
  3. Für das heutige Setup einer Aktie alle historisch gleichen Setups aller
     Aktien sammeln, deren Folge-Kursverläufe auf die aktuelle Volatilität
     der Aktie skalieren und daraus zählen: wie oft stieg der Kurs, wie oft
     wurde eine Knock-out-Schwelle berührt, usw.
"""
import json
import math
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text())
HORIZONS = CFG["horizons"]
LEVERAGES = CFG["leverages"]
MIN_N = CFG.get("min_sample", 150)

TREND_TXT = {1: "Aufwärtstrend", -1: "Abwärtstrend", 0: "Seitwärts / gemischt"}
RSI_TXT = ["überverkauft (<30)", "schwach (30–45)", "neutral (45–55)", "stark (55–70)", "überkauft (>70)"]
EVENT_TXT = {1: "starker Anstieg gestern (>2σ)", -1: "starker Einbruch gestern (>2σ)", 0: "kein Vorfall"}


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def features(df):
    c, h, l = df["Close"], df["High"], df["Low"]
    out = pd.DataFrame(index=df.index)
    out["close"] = c
    out["ret"] = c.pct_change()
    out["sigma"] = out["ret"].rolling(20).std()
    out["sma20"] = c.rolling(20).mean()
    out["sma50"] = c.rolling(50).mean()
    out["sma200"] = c.rolling(200).mean()
    out["rsi"] = rsi(c)
    out["volratio"] = df["Volume"] / df["Volume"].rolling(20).mean()
    out["trend"] = np.where((c > out.sma20) & (out.sma20 > out.sma50), 1,
                            np.where((c < out.sma20) & (out.sma20 < out.sma50), -1, 0))
    out["rsib"] = np.digitize(out["rsi"], [30, 45, 55, 70])
    z = out["ret"] / out["sigma"].shift(1)
    out["z"] = z
    out["event"] = np.where(z > 2, 1, np.where(z < -2, -1, 0))
    for H in HORIZONS:
        fwd = c.shift(-H) / c - 1
        fmin = l.shift(-1).rolling(H).min().shift(-(H - 1)) / c - 1
        fmax = h.shift(-1).rolling(H).max().shift(-(H - 1)) / c - 1
        out[f"r{H}"] = fwd / out.sigma
        out[f"mn{H}"] = fmin / out.sigma
        out[f"mx{H}"] = fmax / out.sigma
    return out


def stats_from(sample, sigma, H):
    r = np.clip(sample[f"r{H}"].values * sigma, -0.99, None)
    mn = np.clip(sample[f"mn{H}"].values * sigma, -0.99, None)
    mx = sample[f"mx{H}"].values * sigma
    res = {
        "p_up": float((r > 0).mean()),
        "p_up5": float((r > 0.05).mean()),
        "p_dn5": float((r < -0.05).mean()),
        "median": float(np.median(r)),
        "q10": float(np.quantile(r, 0.10)),
        "q90": float(np.quantile(r, 0.90)),
        "lev": {},
    }
    for L in LEVERAGES:
        ko_l = mn <= -1 / L
        ko_s = mx >= 1 / L
        res["lev"][str(L)] = {
            "long": {
                "ko": float(ko_l.mean()),
                "win": float((~ko_l & (r > 0)).mean()),
                "ev": float(np.where(ko_l, -1, L * r).mean()),
            },
            "short": {
                "ko": float(ko_s.mean()),
                "win": float((~ko_s & (r < 0)).mean()),
                "ev": float(np.where(ko_s, -1, np.maximum(-L * r, -1)).mean()),
            },
        }
    return res


def fetch_info(t):
    info = {"earnings": None, "news": []}
    try:
        tk = yf.Ticker(t)
        try:
            cal = tk.calendar
            ed = cal.get("Earnings Date") if isinstance(cal, dict) else None
            if ed:
                d = ed[0] if isinstance(ed, (list, tuple)) else ed
                info["earnings"] = str(d)[:10]
        except Exception:
            pass
        try:
            for n in (tk.news or [])[:4]:
                c = n.get("content", n)
                title = c.get("title")
                url = (c.get("canonicalUrl") or {}).get("url") or c.get("link")
                date = c.get("pubDate") or c.get("providerPublishTime")
                if isinstance(date, (int, float)):
                    date = datetime.fromtimestamp(date, timezone.utc).isoformat()
                prov = (c.get("provider") or {}).get("displayName") or c.get("publisher")
                if title:
                    info["news"].append({"title": title, "url": url, "date": (date or "")[:10], "source": prov})
        except Exception:
            pass
    except Exception:
        pass
    if not info["news"]:
        info["news"] = google_news(CFG["tickers"][t])
    return t, info


def google_news(name, limit=4):
    """Fallback: deutsche Schlagzeilen der letzten 3 Tage über Google-News-RSS."""
    import urllib.parse
    import urllib.request
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    try:
        q = urllib.parse.quote(f'"{name}" Aktie when:3d')
        url = f"https://news.google.com/rss/search?q={q}&hl=de&gl=DE&ceid=DE:de"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        root = ET.fromstring(urllib.request.urlopen(req, timeout=10).read())
        out = []
        for it in list(root.iter("item"))[:limit]:
            title = it.findtext("title") or ""
            src = it.findtext("source") or ""
            if src and title.endswith(" - " + src):
                title = title[: -len(src) - 3]
            try:
                date = parsedate_to_datetime(it.findtext("pubDate")).strftime("%Y-%m-%d")
            except Exception:
                date = ""
            out.append({"title": title, "url": it.findtext("link"), "date": date, "source": src})
        return out
    except Exception:
        return []


def drop_unfinished(df, t):
    """Heutige Kerze verwerfen, solange die Börse noch offen ist (Uhrzeit = deutsche Ortszeit)."""
    now = datetime.now(BERLIN)
    if len(df) and df.index[-1].date() == now.date():
        close_hm = (17, 45) if t.endswith(".DE") or t == "^GDAXI" else (22, 15)
        if (now.hour, now.minute) < close_hm:
            return df.iloc[:-1]
    return df


def main():
    tickers = CFG["tickers"]
    market = CFG["market"]
    syms = list(tickers) + list(market)
    print(f"Lade Kursdaten für {len(syms)} Symbole …", flush=True)
    raw = yf.download(syms, period=CFG["history_period"], interval="1d", auto_adjust=True,
                      group_by="ticker", progress=False, threads=True)

    feats = {}
    for t in tickers:
        try:
            df = drop_unfinished(raw[t].dropna(subset=["Close"]), t)
            if len(df) < 250:
                print(f"  ! {t}: zu wenig Daten, übersprungen")
                continue
            feats[t] = features(df)
        except KeyError:
            print(f"  ! {t}: keine Daten")

    pool = pd.concat(feats.values(), ignore_index=True)
    pool = pool.dropna(subset=[f"r{max(HORIZONS)}", "sigma", "sma50"])
    pool = pool[pool.sigma > 0]
    print(f"Historische Vergleichstage gesamt: {len(pool):,}", flush=True)

    print("Lade Termine & Nachrichten …", flush=True)
    with ThreadPoolExecutor(8) as ex:
        infos = dict(ex.map(fetch_info, list(feats)))

    rows = []
    for t, f in feats.items():
        last = f.iloc[-1]
        key = (int(last.trend), int(last.rsib), int(last.event))
        sample = pool[(pool.trend == key[0]) & (pool.rsib == key[1]) & (pool.event == key[2])]
        matched = "Trend + RSI + Vorfall"
        if len(sample) < MIN_N:
            sample = pool[(pool.trend == key[0]) & (pool.rsib == key[1])]
            matched = "Trend + RSI (zu wenige Fälle mit gleichem Vorfall)"
        sigma = float(last.sigma)
        own = f.dropna(subset=[f"r{max(HORIZONS)}", "sigma"])
        row = {
            "ticker": t,
            "name": tickers[t],
            "currency": "EUR" if t.endswith(".DE") else "USD",
            "price": float(last.close),
            "chg1d": float(last.ret),
            "chg5d": float(f.close.iloc[-1] / f.close.iloc[-6] - 1),
            "chg20d": float(f.close.iloc[-1] / f.close.iloc[-21] - 1),
            "sigma": sigma,
            "rsi": float(last.rsi),
            "z": float(last.z),
            "volratio": float(last.volratio) if not math.isnan(last.volratio) else None,
            "above200": bool(last.close > last.sma200) if not math.isnan(last.sma200) else None,
            "trend": key[0], "rsib": key[1], "event": key[2],
            "setup": f"{TREND_TXT[key[0]]} · RSI {RSI_TXT[key[1]]} · {EVENT_TXT[key[2]]}",
            "matched": matched,
            "n": int(len(sample)),
            "spark": [round(float(x), 2) for x in f.close.iloc[-90:]],
            "spark_dates": [d.strftime("%Y-%m-%d") for d in f.index[-90:]],
            "earnings": infos.get(t, {}).get("earnings"),
            "news": infos.get(t, {}).get("news", []),
            "h": {},
            "base": {},
        }
        for H in HORIZONS:
            row["h"][str(H)] = stats_from(sample, sigma, H)
            row["base"][str(H)] = stats_from(pool, sigma, H)
            row["h"][str(H)]["own_p_up"] = float((own[f"r{H}"] > 0).mean())
        rows.append(row)

    mkt = []
    for m, name in market.items():
        try:
            df = drop_unfinished(raw[m].dropna(subset=["Close"]), m)
            c = df["Close"]
            mkt.append({
                "ticker": m, "name": name, "last": float(c.iloc[-1]),
                "chg1d": float(c.iloc[-1] / c.iloc[-2] - 1),
                "chg5d": float(c.iloc[-1] / c.iloc[-6] - 1),
                "above50": bool(c.iloc[-1] > c.rolling(50).mean().iloc[-1]),
                "spark": [round(float(x), 2) for x in c.iloc[-60:]],
            })
        except Exception:
            pass

    last_date = max(f.index[-1] for f in feats.values()).strftime("%Y-%m-%d")
    data = {
        "generated": datetime.now(BERLIN).strftime("%Y-%m-%d %H:%M"),
        "data_date": last_date,
        "horizons": HORIZONS,
        "leverages": LEVERAGES,
        "pool_n": int(len(pool)),
        "rules": CFG.get("signal_rules"),
        "market": mkt,
        "rows": rows,
    }
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / f"scan_{last_date}.json").write_text(json.dumps(data))
    tpl = (ROOT / "template.html").read_text()
    html = tpl.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    (ROOT / "dashboard.html").write_text(html)
    print(f"Fertig: {len(rows)} Aktien → dashboard.html (Kursstand {last_date})")


if __name__ == "__main__":
    sys.exit(main())
