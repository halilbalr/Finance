"""Kronos-prognoser för de handplockade och de största aktierna: data/forecasts.json.

Kör från repots rot, med Kronos-repot utcheckat i kronos-src (eller KRONOS_DIR) och torch
installerat:
    python apps/borskompassen/forecast.py

Kronos är en öppen modell för kursprognoser (github.com/shiyu-coder/Kronos). Varje aktie får
PATHS slumpade prognosbanor för de närmaste HORIZON handelsdagarna; filen sparar mittvärde,
lägsta och högsta bana per dag. Prognoserna är mycket osäkra och visas som ett spann.
Körs högst en gång per dygn: är filen yngre än MAX_AGE_HOURS görs ingenting.
"""

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))

from details import _largest  # noqa: E402

from finance.data import ProviderError, YahooFinance  # noqa: E402

TOP, HORIZON, PATHS, LOOKBACK, MAX_AGE_HOURS = 100, 20, 5, 400, 20


def forecast_one(predict, prices, horizon=HORIZON, paths=PATHS):
    """{dates, median, low, high} från paths prognosbanor. predict(df, x_ts, y_ts) -> DataFrame."""
    frame = prices.tail(LOOKBACK).copy()
    frame["amount"] = frame["close"] * frame["volume"]
    index = pd.DatetimeIndex(frame.index)
    stamps = pd.Series(index.tz_localize(None) if index.tz is not None else index)
    frame = frame.reset_index(drop=True)
    future = pd.Series(pd.bdate_range(stamps.iloc[-1] + pd.Timedelta(days=1), periods=horizon))
    columns = frame[["open", "high", "low", "close", "volume", "amount"]]
    runs = np.array(
        [predict(columns, stamps, future)["close"].to_numpy(float) for _ in range(paths)]
    )
    if not np.isfinite(runs).all() or (runs <= 0).any():
        raise ValueError("Kronos gav ogiltiga värden")
    rnd = lambda values: [round(float(v), 4 if v < 10 else 2) for v in values]  # noqa: E731
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in future],
        "median": rnd(np.median(runs, axis=0)),
        "low": rnd(runs.min(axis=0)),
        "high": rnd(runs.max(axis=0)),
    }


def due(app, now):
    """Sant om prognoserna saknas eller är äldre än MAX_AGE_HOURS."""
    created = _read(app / "data" / "forecasts.json").get("createdAt")
    return not created or now - pd.Timestamp(created) >= pd.Timedelta(hours=MAX_AGE_HOURS)


def build(app, provider, predict, now, model_name="Kronos-small", force=False):
    data = app / "data"
    path = data / "forecasts.json"
    if not force and not due(app, now):
        print(f"Prognoserna från {_read(path)['createdAt']} är aktuella.")
        return
    quotes = json.loads((data / "quotes.json").read_text("utf-8"))["quotes"]
    meta = _read(data / "meta.json")
    fundamentals = _read(app / "universe.json").get("fundamentals", {})
    curated = [
        i["id"] for i in json.loads((app / "instruments.json").read_text("utf-8"))["instruments"]
    ]
    stocks = [q for q in quotes if q.get("type") == "aktie"]
    chosen = _largest(stocks, fundamentals, meta.get("fx", {}), curated, TOP)
    by_id = {q["id"]: q for q in quotes}
    start = (now.normalize() - pd.DateOffset(years=2)).strftime("%Y-%m-%d")
    end = (now.normalize() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    forecasts, errors = {}, []
    for id in chosen:
        quote = by_id.get(id)
        if not quote:
            continue
        try:
            prices = provider.history(quote["symbol"], start, end)
            if len(prices) < 60:
                raise ValueError("för kort historik")
            forecasts[id] = {
                **forecast_one(predict, prices),
                "base": quote["price"], "baseDate": quote["date"], "model": model_name,
            }  # fmt: skip
            print(f"ok   {quote['symbol']}", flush=True)
        except (ProviderError, ValueError) as exc:
            errors.append(f"{quote['symbol']}: {exc}")
            print(f"fel  {quote['symbol']}: {exc}", file=sys.stderr)
    if not forecasts:
        raise SystemExit("Ingen prognos kunde göras; filen lämnas orörd.")
    path.write_text(
        json.dumps({"createdAt": stamp, "model": model_name, "horizon": HORIZON,
                    "forecasts": forecasts, "errors": errors},
                   ensure_ascii=False, separators=(",", ":")),
        "utf-8",
    )  # fmt: skip
    print(f"Klart: {len(forecasts)} prognoser, {len(errors)} fel.")


def _read(path):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def kronos_predictor(kronos_dir):
    """predict(df, x_ts, y_ts) med Kronos-small på CPU."""
    sys.path.insert(0, str(kronos_dir))
    from model import Kronos, KronosPredictor, KronosTokenizer

    tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    model = Kronos.from_pretrained("NeoQuasar/Kronos-small")
    predictor = KronosPredictor(model, tokenizer, max_context=512)

    def predict(frame, stamps, future):
        return predictor.predict(df=frame, x_timestamp=stamps, y_timestamp=future,
                                 pred_len=len(future), T=1.0, top_p=0.9, sample_count=1,
                                 verbose=False)  # fmt: skip

    return predict


if __name__ == "__main__":
    now = pd.Timestamp.now(tz="UTC")
    if "--due" in sys.argv:  # för workflowen: avsluta med 0 om en ny prognos behövs
        raise SystemExit(0 if due(APP, now) else 1)
    predict = kronos_predictor(os.environ.get("KRONOS_DIR", "kronos-src"))
    build(APP, YahooFinance(adjusted=False), predict, now)
