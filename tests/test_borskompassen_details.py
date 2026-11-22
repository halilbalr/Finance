"""Börskompassen's per-instrument detail files and headlines; no network required."""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from finance.data import ProviderError
from finance.indicators import rsi, sma

SPEC = importlib.util.spec_from_file_location(
    "details", Path(__file__).resolve().parents[1] / "apps/borskompassen/details.py"
)
details = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(details)

NOW = pd.Timestamp("2026-11-21 18:00", tz="UTC")


def by_key(signals):
    return {s["key"]: s for s in signals}


def test_rising_prices_give_uptrend_and_overbought():
    closes = list(100 * np.exp(np.linspace(0, 0.8, 260)))
    signals, changes = details.technical(closes)
    found = by_key(signals)
    assert (found["trend"]["title"], found["trend"]["tone"]) == ("Upptrend", 1)
    expected_rsi = round(rsi(pd.Series(closes), 14).iloc[-1])
    assert found["rsi"]["value"] == expected_rsi and expected_rsi >= 70
    assert found["rsi"]["tone"] == -1  # overbought
    assert changes["1 vecka"] == round((closes[-1] / closes[-6] - 1) * 100, 1)
    assert changes["1 år"] == round((closes[-1] / closes[0] - 1) * 100, 1)


def test_falling_prices_give_downtrend_and_recent_death_cross():
    closes = list(np.linspace(100, 150, 200)) + list(np.linspace(150, 90, 60))
    found = by_key(details.technical(closes)[0])
    assert found["trend"]["title"] == "Nedtrend"
    ma = (sma(pd.Series(closes), 50) - sma(pd.Series(closes), 200)).dropna()
    crossed = np.sign(ma.iloc[-1]) != np.sign(ma.iloc[-11])
    assert ("kors" in found) == crossed


def test_short_history_skips_rules_that_need_more_days():
    signals, changes = details.technical([100, 101, 102, 101, 103] * 4)
    assert set(by_key(signals)) == {"bollinger", "rsi"}  # no trend, MACD or volatility yet
    assert list(changes) == ["1 vecka"]  # no month or year from 20 days


def test_key_figures_scale_with_todays_price():
    known = {"price": 100.0, "marketCap": 1e9, "pe": 20.0, "dividendYield": 4.0,
             "priceToBook": 2.0, "eps": 5.0, "analystRating": "2.0 - Buy"}  # fmt: skip
    figures = details.key_figures({"price": 110.0}, known)
    assert figures == {"marketCap": 1.1e9, "pe": 22.0, "dividendYield": 3.64,
                       "priceToBook": 2.2, "eps": 5.0, "analystRating": "2.0 - Buy"}  # fmt: skip
    assert details.key_figures({"price": 110.0}, None) == {}


class FakeYahoo:
    def __init__(self):
        self.asked = []

    def news(self, symbol):
        self.asked.append(symbol)
        if symbol == "BAD.ST":
            raise ProviderError("empty news response")
        return pd.DataFrame([{"title": f"{symbol} nyhet", "url": f"https://x/{symbol}",
                              "published": "2026-11-20T10:00:00Z"}])  # fmt: skip


def feed(url):
    if "riksbank" in url:
        raise ProviderError("No RSS news items found")
    return pd.DataFrame([{"title": url[-12:], "url": url + "/1",
                          "published": pd.Timestamp("2026-11-21 10:00", tz="UTC") if "svt" in url
                          else pd.Timestamp("2026-11-20 10:00", tz="UTC")}])  # fmt: skip


@pytest.fixture
def app(tmp_path):
    def quote(id, symbol, kind="aktie", currency="SEK"):
        closes = list(np.linspace(100, 120, 250))
        return {"id": id, "symbol": symbol, "type": kind, "currency": currency, "price": closes[-1],
                "history": {"dates": [], "close": closes}}  # fmt: skip

    quotes = [quote("volv-b", "VOLV-B.ST"), quote("big-st", "BIG.ST"), quote("bad-st", "BAD.ST"),
              quote("small-st", "SMALL.ST"), quote("fond", "0P1.ST", "fond")]  # fmt: skip
    (tmp_path / "data").mkdir()
    (tmp_path / "data/quotes.json").write_text(json.dumps({"quotes": quotes}))
    (tmp_path / "data/meta.json").write_text(json.dumps({"fx": {"SEK": 1}}))
    (tmp_path / "data/forecasts.json").write_text(
        json.dumps({"forecasts": {"big-st": {"days": 20}}})
    )
    (tmp_path / "data/analyses.json").write_text(
        json.dumps({"analyses": {"volv-b": {"text": "Bra"}}})
    )
    (tmp_path / "instruments.json").write_text(json.dumps({"instruments": [{"id": "volv-b"}]}))
    fundamentals = {"BIG.ST": {"price": 120.0, "marketCap": 9e11}, "BAD.ST": {"price": 120.0, "marketCap": 8e11},
                    "SMALL.ST": {"price": 120.0, "marketCap": 1e6}, "0P1.ST": {"price": 120.0, "netAssets": 5e11}}  # fmt: skip
    (tmp_path / "universe.json").write_text(json.dumps({"fundamentals": fundamentals}))
    return tmp_path


def test_build_writes_detail_files_news_and_headlines(app, monkeypatch):
    monkeypatch.setattr(details, "NEWS_TOP", 2)
    provider = FakeYahoo()
    details.build(app, provider, NOW, fetch_feed=feed)
    read = lambda id: json.loads((app / "data/detail" / f"{id}.json").read_text())  # noqa: E731

    # News for curated + the two largest stocks; funds never take a place.
    assert provider.asked == ["VOLV-B.ST", "BIG.ST", "BAD.ST"]
    assert read("volv-b")["news"] == [
        {"title": "VOLV-B.ST nyhet", "url": "https://x/VOLV-B.ST", "published": "2026-11-20"}
    ]
    assert "news" not in read("small-st") and "news" not in read("fond")
    big = read("big-st")
    assert big["forecast"] == {"days": 20} and big["keyFigures"] == {"marketCap": 9e11}
    assert read("volv-b")["analysis"] == {"text": "Bra"}
    assert (
        big["updatedAt"] == "2026-11-21T18:00:00Z"
        and by_key(big["technical"])["trend"]["tone"] == 1
    )

    heads = json.loads((app / "data/headlines.json").read_text())
    assert [h["source"] for h in heads["headlines"]][0] == "SVT Ekonomi"  # newest first
    assert len(heads["headlines"]) == len(details.FEEDS) - 1
    assert heads["errors"] == ["Riksbanken: No RSS news items found"]


def test_news_failure_is_reported_and_other_files_still_written(app, monkeypatch, capsys):
    monkeypatch.setattr(details, "NEWS_TOP", 3)
    details.build(app, FakeYahoo(), NOW, fetch_feed=feed)
    assert "news" not in json.loads((app / "data/detail/bad-st.json").read_text())
    assert "fel  nyheter BAD.ST" in capsys.readouterr().err
    assert len(list((app / "data/detail").glob("*.json"))) == 5
