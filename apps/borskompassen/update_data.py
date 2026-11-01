"""Hämtar kurser via finance.data.YahooFinance och skriver data/quotes.json och data/meta.json.

Kör från repots rot, med paketet installerat med data-tillägget:
    python -m pip install -e '.[data]'
    python apps/borskompassen/update_data.py

Aktier, fonder och valutor läses från instruments.json bredvid den här filen. Kurserna är
justerade för splittar men inte för utdelningar, så sista stängningen är den noterade kursen.
Om ett instrument inte kan hämtas behålls den senaste lyckade posten (märkt stale) och felet
skrivs i quotes.json. Om inget instrument kan hämtas lämnas filerna orörda.
"""

import json
import math
import sys
from pathlib import Path

import pandas as pd

from finance.data import ProviderError, YahooFinance

APP = Path(__file__).resolve().parent


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


def quote(instrument, provider, start, end, stamp):
    """En post i quotes.json: ett års dagliga stängningar, senaste kurs och nyckeltal."""
    symbol, kind = instrument["symbol"], instrument.get("type", "aktie")
    prices = provider.history(symbol, start, end)
    dates = [day.strftime("%Y-%m-%d") for day in prices.index]
    closes = [_round(value) for value in prices["close"]]
    pe = dividend_yield = None
    try:
        paid = provider.dividends(symbol, start, end).sum()  # utdelningar senaste året
        if paid > 0:
            dividend_yield = _round(paid / prices["close"].iloc[-1] * 100, 2)
        elif kind != "fond":
            dividend_yield = 0
    except ProviderError as exc:
        print(f"     {symbol}: {exc}", file=sys.stderr)
    if kind == "aktie":
        try:
            pe = _round(provider.company(symbol)["pe"], 2)
        except ProviderError as exc:
            print(f"     {symbol}: {exc}", file=sys.stderr)
    return {
        "id": instrument["id"],
        "symbol": symbol,
        "ticker": instrument.get("ticker") or symbol,
        "name": instrument.get("name") or symbol,
        "type": kind,
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


def update(app, provider, now):
    config = json.loads((app / "instruments.json").read_text("utf-8"))
    data = app / "data"
    previous = {q["id"]: q for q in _read(data / "quotes.json", {}).get("quotes", [])}
    previous_meta = _read(data / "meta.json", {})
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    day = now.normalize()
    end = (day + pd.Timedelta(days=1)).strftime("%Y-%m-%d")  # slutdatum ingår inte
    year_ago = (day - pd.DateOffset(years=1)).strftime("%Y-%m-%d")
    week_ago = (day - pd.Timedelta(days=7)).strftime("%Y-%m-%d")

    quotes, errors = [], []
    for instrument in config["instruments"]:
        symbol = instrument["symbol"]
        try:
            quotes.append(quote(instrument, provider, year_ago, end, stamp))
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
            fx[currency] = _round(provider.history(symbol, week_ago, end)["close"].iloc[-1], 4)
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
    print(f"Klart: {fresh} av {len(config['instruments'])} hämtade{failed}.")


if __name__ == "__main__":
    update(APP, YahooFinance(adjusted=False), pd.Timestamp.now(tz="UTC"))
