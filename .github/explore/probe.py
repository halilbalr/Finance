"""Temporary: Kronos forecasts, a local Ollama model and GitHub Models, timed on a runner."""

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd

from finance.data import YahooFinance, rss_news

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
log = {}


def post(url, body, headers):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    t = time.time()
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            raw = response.read().decode("utf-8", "replace")
            return {"status": response.status, "type": response.headers.get("content-type"),
                    "seconds": round(time.time() - t, 1), "raw": raw[:3000]}
    except Exception as exc:
        body = getattr(exc, "read", lambda: b"")().decode("utf-8", "replace")[:600]
        return {"error": repr(exc)[:300], "body": body, "seconds": round(time.time() - t, 1)}


part = sys.argv[2]
if part == "kronos":
    sys.path.insert(0, "kronos-src")
    from model import Kronos, KronosPredictor, KronosTokenizer

    t = time.time()
    tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    model = Kronos.from_pretrained("NeoQuasar/Kronos-small")
    predictor = KronosPredictor(model, tokenizer, max_context=512)
    log["load_s"] = round(time.time() - t, 1)
    provider = YahooFinance(adjusted=False)
    for symbol in ["VOLV-B.ST", "SAAB-B.ST", "AZN.ST", "NVDA", "BTC-USD"]:
        try:
            prices = provider.history(symbol, "2025-06-01", "2026-11-23")
            df = prices.tail(400).reset_index()
            df["amount"] = df["close"] * df["volume"]
            stamps = pd.Series(pd.to_datetime(df["date"]).dt.tz_localize(None))
            future = pd.Series(pd.bdate_range(stamps.iloc[-1] + pd.Timedelta(days=1), periods=20))
            t = time.time()
            pred = predictor.predict(
                df=df[["open", "high", "low", "close", "volume", "amount"]],
                x_timestamp=stamps, y_timestamp=future, pred_len=20,
                T=1.0, top_p=0.9, sample_count=5, verbose=False,
            )
            log[symbol] = {"seconds": round(time.time() - t, 1),
                           "last": round(float(df["close"].iloc[-1]), 2),
                           "pred_close": [round(float(v), 2) for v in pred["close"]]}
        except Exception as exc:
            log[symbol] = repr(exc)[:400]
        print(symbol, log[symbol], flush=True)

if part == "ollama":
    heads = []
    for url in ["https://www.svt.se/nyheter/ekonomi/rss.xml", "https://www.di.se/rss"]:
        heads += [f"- {t}" for t in rss_news(url)["title"].head(12)]
    prompt = (
        "Du skriver omvärldsanalysen i en svensk app för småsparare. Utgå bara från rubrikerna "
        'nedan. Svara med JSON: {"title": kort rubrik, "summary": 2-3 meningar, "items": '
        '[{"title", "what", "why", "impacts": [{"sector": en av Bank, Industri, Försvar, '
        "Teknik, Handel, Läkemedel, Gruvor & metaller, Energi, Spel, Investmentbolag, "
        'Fastigheter, "dir": 1 eller -1, "why"}]}]} med de 3 viktigaste händelserna. '
        "Enkel svenska.\n\nRubriker:\n" + "\n".join(heads)
    )
    log["headlines"] = heads
    for model in sys.argv[3:]:
        log[model] = post(
            "http://localhost:11434/v1/chat/completions",
            {"model": model, "messages": [{"role": "user", "content": prompt}],
             "response_format": {"type": "json_object"}, "temperature": 0.3},
            {"Content-Type": "application/json"},
        )
        print(model, log[model].get("seconds"), flush=True)

if part == "models":
    token = os.environ["GITHUB_TOKEN"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
               "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    body = {"model": "openai/gpt-4.1-mini",
            "messages": [{"role": "user", "content": "Säg hej på svenska."}]}
    log["models.github.ai"] = post("https://models.github.ai/inference/chat/completions", body, headers)

(out / f"{part}.json").write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str))
print(json.dumps(log, ensure_ascii=False, default=str)[:3000])
