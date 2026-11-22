"""Bygger detaljdata per instrument (data/detail/<id>.json) och dagens rubriker (data/headlines.json).

Kör efter update_data.py, från repots rot:
    python apps/borskompassen/details.py

Varje detaljfil har tekniska signaler (räknade med finance.indicators från kurshistoriken i
quotes.json), nyckeltal från universe.json omräknade mot dagens kurs, nyheter från Yahoo för de
handplockade och de största instrumenten, samt Kronos-prognos och AI-analys när de finns
(data/forecasts.json, data/analyses.json). Appen hämtar filen först när aktiesidan öppnas.
"""

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from finance.data import ProviderError, YahooFinance, rss_news
from finance.indicators import bollinger_bands, macd, rsi, sma

APP = Path(__file__).resolve().parent
NEWS_TOP = 150  # så många av de största instrumenten får nyheter, utöver de handplockade
FEEDS = [  # gratis nyhetsflöden för Omvärld
    ("SVT Ekonomi", "https://www.svt.se/nyheter/ekonomi/rss.xml"),
    ("Dagens industri", "https://www.di.se/rss"),
    ("Riksbanken", "https://www.riksbank.se/sv/rss/pressmeddelanden/"),
    ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
]
SCALED = {"marketCap": 1, "forwardPE": 1, "priceToBook": 1, "pe": 1, "dividendYield": -1}


def _num(value, digits=2):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return round(value, digits) if math.isfinite(value) else None


def key_figures(quote, known):
    """Nyckeltal vid dagens kurs. Kursberoende tal skalas med kursen sedan de hämtades."""
    if not known or not known.get("price"):
        return {}
    ratio = quote["price"] / known["price"]
    figures = {}
    for name, value in known.items():
        if name == "price" or value is None:
            continue
        if name in SCALED:
            value = value * ratio if SCALED[name] > 0 else value / ratio
            figures[name] = _num(value, 0 if name == "marketCap" else 2)
        else:
            figures[name] = value if isinstance(value, str) else _num(value, 4)
    return {k: v for k, v in figures.items() if v is not None}


def _change(close, days):
    return (
        None if len(close) <= days else _num((close.iloc[-1] / close.iloc[-1 - days] - 1) * 100, 1)
    )


def technical(closes):
    """Tekniska signaler med svensk förklaring: [{key, title, value, tone, text}].

    tone är 1 (stärkande), -1 (försvagande) eller 0. Varje regel kräver tillräcklig historik
    och hoppas annars över.
    """
    close = pd.Series(closes, dtype=float)
    last, out = close.iloc[-1], []

    def add(key, title, value, tone, text):
        out.append({"key": key, "title": title, "value": value, "tone": tone, "text": text})

    if len(close) >= 200:
        ma50, ma200 = sma(close, 50).iloc[-1], sma(close, 200).iloc[-1]
        if last > ma50 > ma200:
            add(
                "trend",
                "Upptrend",
                None,
                1,
                "Kursen ligger över både 50- och 200-dagars medelvärde, och det korta ligger över det långa.",
            )
        elif last < ma50 < ma200:
            add(
                "trend",
                "Nedtrend",
                None,
                -1,
                "Kursen ligger under både 50- och 200-dagars medelvärde, och det korta ligger under det långa.",
            )
        else:
            add(
                "trend",
                "Ingen tydlig trend",
                None,
                0,
                "Kursen och medelvärdena pekar åt olika håll.",
            )
        cross = (sma(close, 50) - sma(close, 200)).dropna()
        if len(cross) > 10 and np.sign(cross.iloc[-1]) != np.sign(cross.iloc[-11]):
            if cross.iloc[-1] > 0:
                add(
                    "kors",
                    "Gyllene kors",
                    None,
                    1,
                    "50-dagars medelvärdet har nyligen gått upp över 200-dagars. Det tolkas ofta som början på en uppgång.",
                )
            else:
                add(
                    "kors",
                    "Dödskors",
                    None,
                    -1,
                    "50-dagars medelvärdet har nyligen gått ner under 200-dagars. Det tolkas ofta som början på en nedgång.",
                )
    if len(close) >= 35:
        hist = macd(close)["histogram"].dropna()
        recent = hist.iloc[-6:]
        if len(recent) == 6 and np.sign(recent.iloc[0]) != np.sign(recent.iloc[-1]):
            up = recent.iloc[-1] > 0
            add(
                "macd",
                "MACD vände " + ("upp" if up else "ner"),
                None,
                1 if up else -1,
                "Den kortare trenden har den senaste veckan "
                + ("gått om" if up else "fallit under")
                + " den längre (MACD korsade sin signallinje).",
            )
        else:
            up = hist.iloc[-1] > 0
            add(
                "macd",
                "MACD " + ("positiv" if up else "negativ"),
                None,
                1 if up else -1,
                "Den kortare trenden ligger "
                + ("över" if up else "under")
                + " den längre, utan att ha vänt den senaste veckan.",
            )
    if len(close) >= 20:
        bands = bollinger_bands(close).iloc[-1]
        if last > bands["upper"]:
            add(
                "bollinger",
                "Över Bollingerbandet",
                None,
                -1,
                "Kursen har stigit ovanligt mycket jämfört med de senaste 20 dagarna. Det följs ofta av en rekyl.",
            )
        elif last < bands["lower"]:
            add(
                "bollinger",
                "Under Bollingerbandet",
                None,
                1,
                "Kursen har fallit ovanligt mycket jämfört med de senaste 20 dagarna. Det följs ofta av en studs.",
            )
        else:
            add(
                "bollinger",
                "Inom Bollingerbandet",
                None,
                0,
                "Kursen rör sig inom sitt normala intervall för de senaste 20 dagarna.",
            )
    if len(close) >= 15:
        value = _num(rsi(close, 14).iloc[-1], 0)
        tone, text = 0, "Varken överköpt eller översåld."
        if value >= 70:
            tone, text = -1, "Överköpt: kursen har stigit snabbt och kan behöva vila."
        elif value <= 30:
            tone, text = 1, "Översåld: kursen har fallit snabbt och kan studsa."
        add("rsi", "RSI (14 dagar)", value, tone, text)
    if len(close) >= 64:
        vol = _num(np.log(close).diff().iloc[-63:].std() * math.sqrt(252) * 100, 0)
        level = "mycket" if vol >= 40 else "måttligt" if vol >= 20 else "lite"
        add(
            "vol",
            "Svängningar",
            vol,
            0,
            f"Kursen svänger {level}: omkring {vol:.0f} % per år räknat på de senaste tre månaderna.",
        )
    changes = {
        "1 vecka": _change(close, 5),
        "1 månad": _change(close, 21),
        "3 månader": _change(close, 63),
        "1 år": _change(close, len(close) - 1) if len(close) >= 240 else None,
    }
    return out, {k: v for k, v in changes.items() if v is not None}


def _largest(quotes, fundamentals, fx, curated, top):
    """Handplockade id:n plus de top största efter börsvärde/fondkapital i kronor."""
    size = {}
    for q in quotes:
        known = fundamentals.get(q.get("symbol")) or {}
        value = known.get("marketCap") or known.get("netAssets")
        if value:
            size[q["id"]] = value * fx.get(q.get("currency"), 1)
    ranked = sorted(size, key=size.get, reverse=True)[:top]
    return list(dict.fromkeys(list(curated) + ranked))


def news_for(provider, symbol, limit=6):
    frame = provider.news(symbol).head(limit)
    return [
        {"title": r["title"], "url": r["url"], "published": str(r["published"] or "")[:10]}
        for r in frame.to_dict("records")
        if r.get("url")
    ]


def headlines(fetch=rss_news, limit=15):
    """De senaste rubrikerna från FEEDS: [{source, title, url, published}], nyast först."""
    rows, errors = [], []
    for source, url in FEEDS:
        try:
            frame = fetch(url).head(limit)
        except (ProviderError, ValueError) as exc:
            errors.append(f"{source}: {exc}")
            continue
        for r in frame.to_dict("records"):
            rows.append(
                {
                    "source": source,
                    "title": r["title"],
                    "url": r["url"],
                    "published": pd.Timestamp(r["published"]).isoformat(),
                }
            )
    rows.sort(key=lambda r: r["published"], reverse=True)
    return rows, errors


def build(app, provider, now, fetch_feed=rss_news):
    data = app / "data"
    quotes = json.loads((data / "quotes.json").read_text("utf-8"))["quotes"]
    meta = json.loads((data / "meta.json").read_text("utf-8"))
    universe = _read(app / "universe.json")
    config = json.loads((app / "instruments.json").read_text("utf-8"))
    fundamentals = universe.get("fundamentals", {})
    forecasts = _read(data / "forecasts.json").get("forecasts", {})
    analyses = _read(data / "analyses.json").get("analyses", {})
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    curated = [i["id"] for i in config["instruments"]]
    stocks = [q for q in quotes if q.get("type") == "aktie"]  # fonder och index saknar nyheter
    with_news = set(_largest(stocks, fundamentals, meta.get("fx", {}), curated, NEWS_TOP))
    folder = data / "detail"
    folder.mkdir(exist_ok=True)
    failures = 0
    for q in quotes:
        closes = (q.get("history") or {}).get("close") or []
        signals, changes = technical(closes) if len(closes) > 1 else ([], {})
        detail = {
            "id": q["id"],
            "updatedAt": stamp,
            "technical": signals,
            "changes": changes,
            "keyFigures": key_figures(q, fundamentals.get(q.get("symbol"))),
        }
        if q["id"] in with_news and q.get("type") == "aktie":
            try:
                detail["news"] = news_for(provider, q["symbol"])
            except (ProviderError, ValueError) as exc:
                failures += 1
                print(f"fel  nyheter {q['symbol']}: {exc}", file=sys.stderr)
        if q["id"] in forecasts:
            detail["forecast"] = forecasts[q["id"]]
        if q["id"] in analyses:
            detail["analysis"] = analyses[q["id"]]
        _write(folder / f"{q['id']}.json", detail)

    rows, errors = headlines(fetch_feed)
    if rows:  # behåll förra rubrikerna om inget flöde svarar
        _write(data / "headlines.json", {"updatedAt": stamp, "headlines": rows, "errors": errors})
    for error in errors:
        print(f"fel  {error}", file=sys.stderr)
    print(
        f"Klart: {len(quotes)} detaljfiler, nyheter för {len(with_news)} ({failures} fel), {len(rows)} rubriker."
    )


def _read(path):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _write(path, payload):
    text = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    path.write_text(text, "utf-8")


if __name__ == "__main__":
    build(APP, YahooFinance(), pd.Timestamp.now(tz="UTC"))
