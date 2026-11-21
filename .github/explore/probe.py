"""Temporary: check GitHub Models from Actions, Yahoo news per stock and RSS feeds."""

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from finance.data import YahooFinance, rss_news

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
token = os.environ["GITHUB_TOKEN"]
log = {}


def call(url, body=None):
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "Accept": "application/json", "X-GitHub-Api-Version": "2022-11-28"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            headers = {k: v for k, v in response.headers.items() if "ratelimit" in k.lower()}
            return response.status, headers, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode()[:600]


status, headers, catalog = call("https://models.github.ai/catalog/models")
log["catalog_status"] = status
if status == 200:
    log["models"] = [{k: m.get(k) for k in ("id", "rate_limit_tier", "publisher")} for m in catalog]
else:
    log["catalog_error"] = catalog

prompt = [{"role": "user", "content": "Svara på svenska med JSON {\"svar\": ...}: vad betyder P/E-tal, en mening?"}]
for model in ["openai/gpt-4.1-mini", "openai/gpt-4.1", "openai/gpt-4o-mini", "deepseek/DeepSeek-V3-0324"]:
    t = time.time()
    status, headers, body = call("https://models.github.ai/inference/chat/completions",
                                 {"model": model, "messages": prompt, "max_tokens": 200,
                                  "response_format": {"type": "json_object"}})
    text = body["choices"][0]["message"]["content"] if status == 200 else body
    log[f"call {model}"] = {"status": status, "seconds": round(time.time() - t, 1), "rate": headers, "text": text}

provider = YahooFinance()
news = {}
for symbol in ["VOLV-B.ST", "SAAB-B.ST", "AAPL", "EQNR.OL", "NOVO-B.CO", "^OMX"]:
    t = time.time()
    try:
        frame = provider.news(symbol)
        news[symbol] = {"n": len(frame), "seconds": round(time.time() - t, 1),
                        "sample": frame.head(3).astype(str).to_dict("records")}
    except Exception as exc:
        news[symbol] = repr(exc)[:300]
log["yahoo_news"] = news

feeds = {}
for url in ["https://www.svt.se/nyheter/ekonomi/rss.xml", "https://www.di.se/rss",
            "https://feeds.bbci.co.uk/news/business/rss.xml",
            "https://www.cnbc.com/id/100003114/device/rss/rss.html",
            "https://finance.yahoo.com/news/rssindex", "https://www.riksbank.se/sv/rss/pressmeddelanden/"]:
    try:
        frame = rss_news(url)
        feeds[url] = {"n": len(frame), "columns": list(frame), "sample": frame.head(2).astype(str).to_dict("records")}
    except Exception as exc:
        feeds[url] = repr(exc)[:300]
log["rss"] = feeds
(out / "probe.json").write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str))
print(json.dumps({k: v for k, v in log.items() if k != "models"}, ensure_ascii=False, default=str)[:4000])
