"""Börskompassen's data files keep the exact format the app reads; no network required."""

import importlib.util
import json
import re
import shutil
from pathlib import Path

import pandas as pd
import pytest

from finance.data import ProviderError

APP = Path(__file__).resolve().parents[1] / "apps/borskompassen"
SPEC = importlib.util.spec_from_file_location("update_data", APP / "update_data.py")
update_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(update_data)

NOW = pd.Timestamp("2021-06-30 16:00", tz="UTC")
CONFIG = {
    "fx": {"USD": "USDSEK=X", "NOK": "NOKSEK=X"},
    "instruments": [
        {
            "id": "volv-b",
            "symbol": "VOLV-B.ST",
            "ticker": "VOLV B",
            "name": "Volvo B",
            "type": "aktie",
            "sector": "Industri",
            "about": "Lastbilar",
        },
        {"id": "fond-global", "symbol": "URTH", "type": "fond", "sector": "Globalfond"},
    ],
}
QUOTE_KEYS = [
    "id", "symbol", "ticker", "name", "type", "sector", "currency", "price", "prevClose",
    "date", "high52", "low52", "pe", "dividendYield", "about", "history", "demo", "updatedAt",
]  # fmt: skip


class FakeYahoo:
    """The YahooFinance methods update_data calls, with an optional set of failing symbols."""

    def __init__(self, ohlcv, failing=(), paid=18.0):
        self.ohlcv, self.failing, self.paid, self.company_calls = ohlcv, set(failing), paid, []

    def history(self, symbol, start, end):
        if symbol in self.failing:
            raise ProviderError(f"Yahoo history failed for {symbol}")
        index = self.ohlcv.index
        result = self.ohlcv[(index >= start) & (index < end)].copy()  # end is exclusive
        result.attrs["currency"] = "USD" if symbol in ("URTH", "USDSEK=X") else "SEK"
        return result

    def dividends(self, symbol, start, end):
        amounts = [self.paid] if symbol == "VOLV-B.ST" else []
        return pd.Series(amounts, dtype=float, name="dividend")

    def company(self, symbol):
        self.company_calls.append(symbol)
        return pd.Series({"pe": 12.345, "dividend_yield": 0.05})


@pytest.fixture
def app(tmp_path):
    (tmp_path / "instruments.json").write_text(json.dumps(CONFIG), "utf-8")
    return tmp_path


def read(app, name):
    return json.loads((app / "data" / name).read_text("utf-8"))


def test_quotes_and_meta_match_the_app_format(app, ohlcv):
    provider = FakeYahoo(ohlcv)
    update_data.update(app, provider, NOW)
    quotes, meta = read(app, "quotes.json"), read(app, "meta.json")

    assert list(quotes) == ["updatedAt", "source", "quotes", "errors"]
    assert (quotes["updatedAt"], quotes["source"]) == ("2021-06-30T16:00:00Z", "Yahoo Finance")
    assert quotes["errors"] == []
    stock, fund = quotes["quotes"]
    for quote in (stock, fund):
        assert list(quote) == QUOTE_KEYS
        history = quote["history"]
        assert len(history["dates"]) == len(history["close"]) >= 200
        assert history["dates"] == sorted(set(history["dates"]))
        assert history["start"] == history["dates"][0] >= "2020-06-30"
        assert quote["date"] == history["dates"][-1] == "2021-06-30"
        assert quote["price"] == history["close"][-1]
        assert quote["prevClose"] == history["close"][-2]
        assert quote["low52"] <= quote["price"] <= quote["high52"]
        assert (quote["demo"], quote["updatedAt"]) == (False, "2021-06-30T16:00:00Z")

    year = ohlcv.loc["2020-06-30":"2021-06-30"]
    assert stock["price"] == round(year.close.iloc[-1], 2)  # 2 decimals between 10 and 1000
    assert stock["high52"] == round(year.high.max(), 2)
    assert stock["currency"] == "SEK" and fund["currency"] == "USD"  # from the provider
    assert stock["dividendYield"] == round(18 / year.close.iloc[-1] * 100, 2)
    assert stock["pe"] == 12.35
    # Defaults for fields an instrument leaves out; funds have no P/E and here no dividend.
    assert (fund["ticker"], fund["name"], fund["about"]) == ("URTH", "URTH", "")
    assert (fund["pe"], fund["dividendYield"]) == (None, None)
    assert provider.company_calls == ["VOLV-B.ST"]

    usd = round(year.close.iloc[-1], 4)
    assert meta == {
        "pricesDemo": False,
        "pricesUpdatedAt": "2021-06-30T16:00:00Z",
        "source": "Yahoo Finance",
        "fx": {"SEK": 1, "USD": usd, "NOK": usd},
        "fxDemo": False,
    }


def test_stock_without_dividends_yields_zero(app, ohlcv):
    update_data.update(app, FakeYahoo(ohlcv, paid=0.0), NOW)
    assert read(app, "quotes.json")["quotes"][0]["dividendYield"] == 0


def test_failed_instrument_keeps_previous_entry_marked_stale(app, ohlcv):
    data = app / "data"
    data.mkdir()
    old = {"id": "fond-global", "price": 100, "demo": True}
    (data / "quotes.json").write_text(json.dumps({"quotes": [old]}), "utf-8")
    note = {"pricesDemo": True, "pricesNote": "Exempelkurser", "fx": {"SEK": 1, "USD": 9}}
    (data / "meta.json").write_text(json.dumps({**note, "fxDemo": True}), "utf-8")
    update_data.update(app, FakeYahoo(ohlcv, failing={"URTH"}), NOW)

    quotes, meta = read(app, "quotes.json"), read(app, "meta.json")
    assert quotes["quotes"][1] == {**old, "stale": True}
    assert quotes["errors"] == [
        {"id": "fond-global", "symbol": "URTH", "error": "Yahoo history failed for URTH"}
    ]
    assert meta["pricesDemo"] is False and meta["pricesNote"] == "Exempelkurser"
    assert meta["fxDemo"] is False and meta["fx"]["USD"] != 9  # demo rates are replaced


def test_failed_rate_keeps_previous_real_rate(app, ohlcv):
    data = app / "data"
    data.mkdir()
    previous = {"fx": {"SEK": 1, "USD": 9.5, "NOK": 0.9}, "fxDemo": False}
    (data / "meta.json").write_text(json.dumps(previous), "utf-8")
    update_data.update(app, FakeYahoo(ohlcv, failing={"NOKSEK=X"}), NOW)

    meta = read(app, "meta.json")
    assert meta["fx"]["NOK"] == 0.9 and meta["fx"]["USD"] != 9.5
    assert meta["fxDemo"] is False
    error = {"id": "fx-NOK", "symbol": "NOKSEK=X", "error": "Yahoo history failed for NOKSEK=X"}
    assert read(app, "quotes.json")["errors"] == [error]


def test_failed_rate_after_demo_rates_stays_demo(app, ohlcv):
    data = app / "data"
    data.mkdir()
    previous = {"fx": {"SEK": 1, "USD": 9.55, "NOK": 0.94}, "fxDemo": True}
    (data / "meta.json").write_text(json.dumps(previous), "utf-8")
    update_data.update(app, FakeYahoo(ohlcv, failing={"NOKSEK=X"}), NOW)

    meta = read(app, "meta.json")
    assert "NOK" not in meta["fx"]  # the app falls back to its own default rate
    assert meta["fxDemo"] is True


def test_nothing_is_written_when_every_instrument_fails(app, ohlcv):
    failing = {item["symbol"] for item in CONFIG["instruments"]}
    with pytest.raises(SystemExit, match="Ingen kurs"):
        update_data.update(app, FakeYahoo(ohlcv, failing=failing), NOW)
    assert not (app / "data").exists()


def test_shipped_app_updates_and_keeps_example_ids(tmp_path, ohlcv):
    """The real instruments.json covers the ids the example portfolio and watch list use."""
    html = (APP / "index.html").read_text("utf-8")
    trades = re.search(r"const EXAMPLE_TRADES = \[(.*?)\];", html, re.S).group(1)
    watch = re.search(r"watch: \[([^\]]*)\]", html).group(1)
    used = set(re.findall(r"inst: '([^']+)'", trades)) | set(re.findall(r"'([^']+)'", watch))

    shutil.copytree(APP, tmp_path / "app", ignore=shutil.ignore_patterns("__pycache__"))
    update_data.update(tmp_path / "app", FakeYahoo(ohlcv), NOW)
    quotes = read(tmp_path / "app", "quotes.json")
    ids = {quote["id"] for quote in quotes["quotes"]}
    assert used and used <= ids
    assert quotes["errors"] == [] and not any(q["demo"] for q in quotes["quotes"])
    assert read(tmp_path / "app", "meta.json")["fxDemo"] is False
