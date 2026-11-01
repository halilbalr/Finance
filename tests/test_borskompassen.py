"""Börskompassen's data files keep the exact format the app reads; no network required."""

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from finance.data import ProviderError

SPEC = importlib.util.spec_from_file_location(
    "update_data", Path(__file__).resolve().parents[1] / "apps/borskompassen/update_data.py"
)
update_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(update_data)

NOW = pd.Timestamp("2021-06-30 16:00", tz="UTC")
INSTRUMENTS = [
    {
        "id": "volv-b",
        "symbol": "VOLV-B.ST",
        "ticker": "VOLV B",
        "name": "Volvo B",
        "type": "aktie",
        "sector": "Industri",
        "currency": "SEK",
        "about": "Lastbilar",
    },
    {
        "id": "fond-global",
        "symbol": "0P0000J1JP.ST",
        "ticker": "GLOBAL",
        "name": "Globalfond",
        "type": "fond",
        "sector": "Globalfond",
        "currency": "SEK",
        "about": "Fond",
    },
]
QUOTE_KEYS = [
    "id", "symbol", "ticker", "name", "type", "sector", "currency", "price", "prevClose",
    "date", "high52", "low52", "pe", "dividendYield", "about", "history", "demo", "updatedAt",
]  # fmt: skip


class FakeYahoo:
    def __init__(self, ohlcv, failing=()):
        self.ohlcv, self.failing, self.company_calls = ohlcv, set(failing), []

    def history(self, ticker, start, end):
        if ticker in self.failing:
            raise ProviderError(f"Yahoo history failed for {ticker}")
        index = self.ohlcv.index
        return self.ohlcv[(index >= start) & (index < end)]  # end is exclusive, as in Yahoo

    def company(self, ticker):
        self.company_calls.append(ticker)
        return pd.Series({"pe": 12.345, "dividend_yield": 0.0621})


@pytest.fixture
def app(tmp_path):
    (tmp_path / "instruments.json").write_text(json.dumps(INSTRUMENTS), "utf-8")
    return tmp_path


def read(app, name):
    return json.loads((app / "data" / name).read_text("utf-8"))


def test_quotes_and_meta_match_the_app_format(app, ohlcv):
    provider = FakeYahoo(ohlcv)
    update_data.update(app, provider, NOW)
    quotes, meta = read(app, "quotes.json"), read(app, "meta.json")

    assert list(quotes) == ["updatedAt", "source", "quotes", "errors"]
    assert quotes["source"] == "Yahoo Finance" and quotes["errors"] == []
    stock, fund = quotes["quotes"]
    for quote in (stock, fund):
        assert list(quote) == QUOTE_KEYS
        history = quote["history"]
        assert len(history["dates"]) == len(history["close"]) >= 200
        assert history["dates"] == sorted(set(history["dates"]))
        assert history["start"] == history["dates"][0] >= "2020-06-30"
        assert quote["date"] == history["dates"][-1] <= "2021-06-30"
        assert quote["price"] == history["close"][-1]
        assert quote["prevClose"] == history["close"][-2]
        assert quote["low52"] <= quote["price"] <= quote["high52"]
        assert quote["demo"] is False

    expected = ohlcv.loc["2020-06-30":"2021-06-30"]
    assert stock["price"] == pytest.approx(expected.close.iloc[-1], abs=1e-4)
    assert stock["high52"] == pytest.approx(expected.high.max(), abs=1e-4)
    assert (stock["pe"], stock["dividendYield"]) == (12.35, 6.21)  # fraction -> percent
    assert (fund["pe"], fund["dividendYield"]) == (None, None)
    assert provider.company_calls == ["VOLV-B.ST"]  # funds have no company snapshot

    assert meta == {
        "pricesDemo": False,
        "pricesUpdatedAt": "2021-06-30T16:00:00Z",
        "source": "Yahoo Finance",
        "fx": {"SEK": 1, "USD": stock["price"], "NOK": stock["price"], "EUR": stock["price"]},
        "fxDemo": False,
    }


def test_failed_instrument_keeps_previous_entry_and_reports_error(app, ohlcv):
    data = app / "data"
    data.mkdir()
    old_fund = {"id": "fond-global", "price": 100, "demo": True}
    (data / "quotes.json").write_text(json.dumps({"quotes": [old_fund]}), "utf-8")
    update_data.update(app, FakeYahoo(ohlcv, failing={"0P0000J1JP.ST"}), NOW)

    quotes, meta = read(app, "quotes.json"), read(app, "meta.json")
    assert quotes["quotes"][1] == old_fund
    assert quotes["errors"] == ["0P0000J1JP.ST: Yahoo history failed for 0P0000J1JP.ST"]
    assert meta["pricesDemo"] is True  # the retained entry is still demo data


def test_failed_fx_uses_previous_rate(app, ohlcv):
    data = app / "data"
    data.mkdir()
    previous = {"fx": {"SEK": 1, "USD": 9.5, "NOK": 0.9, "EUR": 11}, "fxDemo": True}
    (data / "meta.json").write_text(json.dumps(previous), "utf-8")
    update_data.update(app, FakeYahoo(ohlcv, failing={"NOKSEK=X"}), NOW)

    meta = read(app, "meta.json")
    assert meta["fx"]["NOK"] == 0.9 and meta["fx"]["USD"] != 9.5
    assert meta["fxDemo"] is True
    assert read(app, "quotes.json")["errors"] == ["NOKSEK=X: Yahoo history failed for NOKSEK=X"]


def test_nothing_is_written_when_every_instrument_fails(app, ohlcv):
    failing = {item["symbol"] for item in INSTRUMENTS}
    with pytest.raises(SystemExit, match="No instrument"):
        update_data.update(app, FakeYahoo(ohlcv, failing=failing), NOW)
    assert not (app / "data" / "quotes.json").exists()
