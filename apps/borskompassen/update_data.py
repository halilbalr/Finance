"""Write Börskompassen's data/quotes.json and data/meta.json from Yahoo Finance.

Run from the repository root with the data extra: python apps/borskompassen/update_data.py
Instruments come from instruments.json next to this file. Prices are split-adjusted but not
dividend-adjusted, matching the quoted price. An instrument whose history fails keeps its
previous entry (and its demo flag); nothing is written when every instrument fails.
"""

import json
import math
import sys
from pathlib import Path

import pandas as pd

from finance.data import ProviderError, YahooFinance

APP = Path(__file__).resolve().parent
CURRENCIES = ["USD", "NOK", "EUR"]  # kronor per unit; SEK is 1


def _number(value, digits):
    """Round a provider float for JSON; missing or nonfinite values become null."""
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def _read(path, default):
    return json.loads(path.read_text("utf-8")) if path.exists() else default


def _write(path, payload):
    text = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    path.write_text(text + "\n", "utf-8")


def quote(instrument, provider, start, end, stamp):
    """One quotes.json entry: a year of daily closes plus the latest price and fundamentals."""
    prices = provider.history(instrument["symbol"], start, end)
    dates = [day.strftime("%Y-%m-%d") for day in prices.index]
    closes = [_number(value, 4) for value in prices["close"]]
    pe = dividend_yield = None
    if instrument.get("type") == "aktie":
        try:
            company = provider.company(instrument["symbol"])
            pe = _number(company["pe"], 2)
            dividend_yield = _number(company["dividend_yield"] * 100, 2)
        except ProviderError as exc:
            print(f"{instrument['symbol']}: {exc}", file=sys.stderr)
    return {
        "id": instrument["id"],
        "symbol": instrument["symbol"],
        "ticker": instrument.get("ticker"),
        "name": instrument.get("name"),
        "type": instrument.get("type"),
        "sector": instrument.get("sector"),
        "currency": instrument.get("currency"),
        "price": closes[-1],
        "prevClose": closes[-2] if len(closes) > 1 else None,
        "date": dates[-1],
        "high52": _number(prices["high"].max(), 4),
        "low52": _number(prices["low"].min(), 4),
        "pe": pe,
        "dividendYield": dividend_yield,
        "about": instrument.get("about"),
        "history": {"start": dates[0], "dates": dates, "close": closes},
        "demo": False,
        "updatedAt": stamp,
    }


def update(app, provider, now):
    instruments = json.loads((app / "instruments.json").read_text("utf-8"))
    if isinstance(instruments, dict):
        instruments = instruments["instruments"]
    data = app / "data"
    data.mkdir(exist_ok=True)
    previous = {q["id"]: q for q in _read(data / "quotes.json", {"quotes": []})["quotes"]}
    previous_meta = _read(data / "meta.json", {})
    start = (now.normalize() - pd.DateOffset(years=1)).strftime("%Y-%m-%d")
    end = (now.normalize() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    quotes, errors, live = [], [], 0
    for instrument in instruments:
        try:
            quotes.append(quote(instrument, provider, start, end, stamp))
            live += 1
        except ProviderError as exc:
            errors.append(f"{instrument['symbol']}: {exc}")
            if instrument["id"] in previous:
                quotes.append(previous[instrument["id"]])
    if not live:
        raise SystemExit(
            "No instrument could be fetched; data files left unchanged.\n" + "\n".join(errors)
        )

    fx, fx_fallback = {"SEK": 1}, False
    for currency in CURRENCIES:
        try:
            rates = provider.history(f"{currency}SEK=X", start, end)
            fx[currency] = _number(rates["close"].iloc[-1], 4)
        except ProviderError as exc:
            if currency not in previous_meta.get("fx", {}):
                raise SystemExit(f"{currency}SEK unavailable and no previous rate: {exc}") from exc
            errors.append(f"{currency}SEK=X: {exc}")
            fx[currency], fx_fallback = previous_meta["fx"][currency], True

    _write(
        data / "quotes.json",
        {
            "updatedAt": stamp,
            "source": "Yahoo Finance",
            "quotes": quotes,
            "errors": errors,
        },
    )
    _write(
        data / "meta.json",
        {
            "pricesDemo": any(q.get("demo") for q in quotes),
            "pricesUpdatedAt": stamp,
            "source": "Yahoo Finance",
            "fx": fx,
            "fxDemo": fx_fallback and previous_meta.get("fxDemo", False),
        },
    )
    for error in errors:
        print(error, file=sys.stderr)
    print(f"Wrote {live} of {len(instruments)} instruments, {len(errors)} error(s).")


if __name__ == "__main__":
    update(APP, YahooFinance(adjusted=False), pd.Timestamp.now(tz="UTC"))
