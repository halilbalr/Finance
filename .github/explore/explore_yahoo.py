"""Temporary: record what Yahoo's screener returns, for designing Börskompassen's universe."""

import json
import sys
import time
from collections import Counter
from pathlib import Path

import yfinance as yf
from yfinance import EquityQuery, ETFQuery, FundQuery
from yfinance.const import SECTOR_INDUSTY_MAPPING

from finance.data import YahooFinance, sp500_constituents

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
log = {}


def page_all(query, limit=4000):
    rows, offset, total = [], 0, None
    while offset < limit:
        try:
            response = yf.screen(query, offset=offset, size=250, sortField="ticker", sortAsc=True)
        except Exception as exc:  # record and stop this query
            log.setdefault("errors", []).append(f"{query}: {exc!r}")
            break
        quotes = response.get("quotes", [])
        total = response.get("total", total)
        rows += quotes
        offset += len(quotes)
        if not quotes or offset >= (total or 0):
            break
        time.sleep(0.3)
    return rows, total


def save(name, data):
    (out / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str))


started = time.time()
for exchange in ["STO", "OSL", "HEL", "CPH"]:
    rows, total = page_all(EquityQuery("eq", ["exchange", exchange]))
    log[exchange] = {
        "total": total,
        "fetched": len(rows),
        "quoteTypes": Counter(r.get("quoteType") for r in rows),
        "keys": sorted(set().union(*[r.keys() for r in rows])) if rows else [],
        "noMarketCap": sum(r.get("marketCap") is None for r in rows),
    }
    save(f"equities_{exchange}", rows)
    print(exchange, log[exchange]["total"], log[exchange]["fetched"], flush=True)

industries = {}
for sector, names in SECTOR_INDUSTY_MAPPING.items():
    for industry in sorted(names):
        query = EquityQuery(
            "and", [EquityQuery("eq", ["exchange", "STO"]), EquityQuery("eq", ["industry", industry])]
        )
        rows, total = page_all(query)
        if rows:
            industries[f"{sector}|{industry}"] = [r["symbol"] for r in rows]
        time.sleep(0.2)
save("industries_STO", industries)
log["industries_STO_symbols"] = sum(len(v) for v in industries.values())
print("industries", log["industries_STO_symbols"], flush=True)

for name, query in [
    ("funds_STO", FundQuery("eq", ["exchange", "STO"])),
    ("etfs_STO", ETFQuery("eq", ["exchange", "STO"])),
]:
    rows, total = page_all(query)
    log[name] = {"total": total, "fetched": len(rows), "keys": sorted(set().union(*[r.keys() for r in rows])) if rows else []}
    save(name, rows)
    print(name, total, len(rows), flush=True)

searches = {}
for text in ["Avanza Global", "Länsförsäkringar Global Indexnära", "Spiltan Investmentbolag", "SPP Aktiefond Sverige", "Handelsbanken Sverigefond"]:
    try:
        searches[text] = [
            {k: q.get(k) for k in ["symbol", "shortname", "longname", "quoteType", "exchange", "typeDisp"]}
            for q in yf.Search(text, max_results=8).quotes
        ]
    except Exception as exc:
        searches[text] = repr(exc)
save("searches", searches)

try:
    sp = sp500_constituents()
    log["sp500"] = {"count": len(sp), "sectors": Counter(sp.sector)}
except Exception as exc:
    log["sp500"] = repr(exc)

provider = YahooFinance(adjusted=False)
sample = [r["symbol"] for r in json.loads((out / "equities_STO.json").read_text())[:40]]
timing, failures = [], {}
for symbol in sample:
    t = time.time()
    try:
        provider.history(symbol, "2025-11-01", "2026-11-03")
    except Exception as exc:
        failures[symbol] = str(exc)[:200]
    timing.append(round(time.time() - t, 3))
log["history_timing"] = {"n": len(timing), "total_s": round(sum(timing), 1), "max_s": max(timing, default=0), "failures": failures}
log["elapsed_s"] = round(time.time() - started, 1)
save("log", log)
print(json.dumps(log, default=str)[:3000])
