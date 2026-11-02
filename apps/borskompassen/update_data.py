"""Hämtar kurser via finance.data.YahooFinance och skriver data/quotes.json och data/meta.json.

Kör från repots rot, med paketet installerat med data-tillägget:
    python -m pip install -e '.[data]'
    python apps/borskompassen/update_data.py

Aktier, fonder och valutor läses från instruments.json bredvid den här filen, och från
universe.json om build_universe.py har byggt den. Kurserna är justerade för splittar men inte
för utdelningar, så sista stängningen är den noterade kursen.
Om ett instrument inte kan hämtas behålls den senaste lyckade posten (märkt stale) och felet
skrivs i quotes.json. Om inget instrument kan hämtas lämnas filerna orörda.
"""

import json
import math
import sys
import time
from pathlib import Path

import pandas as pd

from finance.data import ProviderError, YahooFinance

APP = Path(__file__).resolve().parent
PAUSE = 60  # sekunder att vänta när Yahoo stryper anropen


def _round(value, digits=None):
    """Avrunda för JSON (4, 2 eller 1 decimal efter storlek); saknat eller ogiltigt blir null."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    if digits is None:
        digits = 4 if abs(value) < 10 else 2 if abs(value) < 1000 else 1
    return round(value, digits)


def _read(path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def _history(provider, symbol, start, end):
    """Kurshistorik. Stryper Yahoo antalet anrop väntar vi en stund och försöker en gång till."""
    try:
        return provider.history(symbol, start, end)
    except ProviderError as exc:
        if not any(word in str(exc).lower() for word in ("rate limit", "too many requests")):
            raise
        print(f"     Yahoo stryper anropen, väntar {PAUSE} s", file=sys.stderr)
        time.sleep(PAUSE)
        return provider.history(symbol, start, end)


def quote(instrument, provider, start, end, stamp, fundamentals):
    """En post i quotes.json: ett års dagliga stängningar, senaste kurs och nyckeltal.

    fundamentals[symbol] = {pe, dividendYield, price} från build_universe.py. Vinst och
    utdelning per aktie ändras sällan, så båda räknas om mot dagens kurs.
    """
    symbol = instrument["symbol"]
    prices = _history(provider, symbol, start, end)
    dates = [day.strftime("%Y-%m-%d") for day in prices.index]
    closes = [_round(value) for value in prices["close"]]
    known = fundamentals.get(symbol) or {}
    ratio = float(prices["close"].iloc[-1]) / known["price"] if known.get("price") else None
    pe = dividend_yield = None
    if ratio and known.get("pe") is not None:
        pe = _round(known["pe"] * ratio, 2)
    if ratio and known.get("dividendYield") is not None:
        dividend_yield = _round(known["dividendYield"] / ratio, 2)
    return {
        "id": instrument["id"],
        "symbol": symbol,
        "ticker": instrument.get("ticker") or symbol,
        "name": instrument.get("name") or symbol,
        "type": instrument.get("type", "aktie"),
        "sector": instrument.get("sector") or "Annat",
        "currency": prices.attrs.get("currency") or "SEK",
        "price": closes[-1],
        "prevClose": closes[-2] if len(closes) > 1 else None,
        "date": dates[-1],
        "high52": _round(prices["high"].max()),
        "low52": _round(prices["low"].min()),
        "pe": pe,
        "dividendYield": dividend_yield,
        "about": instrument.get("about") or "",
        "history": {"start": dates[0], "dates": dates, "close": closes},
        "demo": False,
        "updatedAt": stamp,
    }


def instruments(app):
    """instruments.json först, sedan universe.json utan dubbletter, och nyckeltalen."""
    config = json.loads((app / "instruments.json").read_text("utf-8"))
    universe = _read(app / "universe.json", {})
    ids = {i["id"] for i in config["instruments"]}
    symbols = {i["symbol"] for i in config["instruments"]}
    extra = [
        i
        for i in universe.get("instruments", [])
        if i["id"] not in ids and i["symbol"] not in symbols
    ]
    return config, config["instruments"] + extra, universe.get("fundamentals", {})


def update(app, provider, now):
    config, followed, fundamentals = instruments(app)
    data = app / "data"
    previous = {q["id"]: q for q in _read(data / "quotes.json", {}).get("quotes", [])}
    previous_meta = _read(data / "meta.json", {})
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    day = now.normalize()
    end = (day + pd.Timedelta(days=1)).strftime("%Y-%m-%d")  # slutdatum ingår inte
    year_ago = (day - pd.DateOffset(years=1)).strftime("%Y-%m-%d")
    week_ago = (day - pd.Timedelta(days=7)).strftime("%Y-%m-%d")

    quotes, errors = [], []
    for instrument in followed:
        symbol = instrument["symbol"]
        try:
            quotes.append(quote(instrument, provider, year_ago, end, stamp, fundamentals))
            print(f"ok   {symbol}")
        except (ProviderError, ValueError) as exc:
            errors.append({"id": instrument["id"], "symbol": symbol, "error": str(exc)})
            print(f"fel  {symbol}: {exc}", file=sys.stderr)
            if instrument["id"] in previous:  # även exempelkurser, så att ett innehav finns kvar
                quotes.append({**previous[instrument["id"]], "stale": True})
    fresh = sum(not q.get("stale") for q in quotes)
    if not fresh:
        raise SystemExit("Ingen kurs kunde hämtas. Filerna lämnas orörda.")

    fx = {"SEK": 1, **({} if previous_meta.get("fxDemo") else previous_meta.get("fx", {}))}
    fx_ok = True
    for currency, symbol in config.get("fx", {}).items():
        try:
            fx[currency] = _round(_history(provider, symbol, week_ago, end)["close"].iloc[-1], 4)
        except (ProviderError, ValueError) as exc:
            fx_ok = False
            errors.append({"id": f"fx-{currency}", "symbol": symbol, "error": str(exc)})
            print(f"fel  {symbol}: {exc}", file=sys.stderr)

    data.mkdir(exist_ok=True)
    payload = {"updatedAt": stamp, "source": "Yahoo Finance", "quotes": quotes, "errors": errors}
    (data / "quotes.json").write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")), "utf-8"
    )
    meta = {
        **previous_meta,
        "pricesDemo": False,
        "pricesUpdatedAt": stamp,
        "source": "Yahoo Finance",
        "fx": fx,
        "fxDemo": not fx_ok and bool(previous_meta.get("fxDemo")),
    }
    (data / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    failed = f", {len(errors)} fel" if errors else ""
    print(f"Klart: {fresh} av {len(followed)} hämtade{failed}.")


if __name__ == "__main__":
    update(APP, YahooFinance(adjusted=False), pd.Timestamp.now(tz="UTC"))
