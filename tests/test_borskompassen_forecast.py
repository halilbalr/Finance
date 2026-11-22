"""Börskompassen's Kronos forecasts, with a fake predictor; no model or network required."""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from finance.data import ProviderError

APP = Path(__file__).resolve().parents[1] / "apps/borskompassen"
SPEC = importlib.util.spec_from_file_location("forecast", APP / "forecast.py")
forecast = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(forecast)

NOW = pd.Timestamp("2026-11-20 07:30", tz="UTC")  # a Friday


class FakePredictor:
    """Path k predicts last close * (1 + k%) every day, k = 0..paths-1."""

    def __init__(self):
        self.calls = 0

    def __call__(self, frame, stamps, future):
        assert list(frame) == ["open", "high", "low", "close", "volume", "amount"]
        assert len(frame) <= forecast.LOOKBACK and stamps.dt.tz is None
        k, self.calls = self.calls % forecast.PATHS, self.calls + 1
        return pd.DataFrame({"close": [frame["close"].iloc[-1] * (1 + k / 100)] * len(future)})


class FakeYahoo:
    def __init__(self, ohlcv, failing=()):
        self.ohlcv, self.failing, self.asked = ohlcv, set(failing), []

    def history(self, symbol, start, end):
        self.asked.append(symbol)
        if symbol in self.failing:
            raise ProviderError(f"Yahoo history failed for {symbol}")
        return self.ohlcv.tz_localize("Europe/Stockholm").rename_axis("date")


def test_forecast_one_summarises_paths(ohlcv):
    result = forecast.forecast_one(FakePredictor(), ohlcv)
    last = ohlcv["close"].iloc[-1]
    next_day = ohlcv.index[-1] + pd.offsets.BDay(1)
    assert result["dates"][0] == next_day.strftime("%Y-%m-%d")
    assert len(result["dates"]) == forecast.HORIZON
    assert result["median"][0] == round(last * 1.02, 2)
    assert (result["low"][0], result["high"][0]) == (round(last, 2), round(last * 1.04, 2))


def test_forecast_one_rejects_invalid_model_output(ohlcv):
    def broken(frame, stamps, future):
        return pd.DataFrame({"close": [np.nan] * len(future)})

    with pytest.raises(ValueError, match="ogiltiga"):
        forecast.forecast_one(broken, ohlcv)


@pytest.fixture
def app(tmp_path):
    quotes = [{"id": i, "symbol": s, "type": t, "price": 100.0, "date": "2026-11-19", "currency": "SEK"}
              for i, s, t in [("volv-b", "VOLV-B.ST", "aktie"), ("big", "BIG.ST", "aktie"),
                              ("small", "SMALL.ST", "aktie"), ("fund", "0P1.ST", "fond")]]  # fmt: skip
    (tmp_path / "data").mkdir()
    (tmp_path / "data/quotes.json").write_text(json.dumps({"quotes": quotes}))
    (tmp_path / "data/meta.json").write_text(json.dumps({"fx": {"SEK": 1}}))
    (tmp_path / "instruments.json").write_text(json.dumps({"instruments": [{"id": "volv-b"}]}))
    fundamentals = {"BIG.ST": {"price": 100, "marketCap": 9e11}, "SMALL.ST": {"price": 100, "marketCap": 1e6},
                    "0P1.ST": {"price": 100, "netAssets": 9e12}}  # fmt: skip
    (tmp_path / "universe.json").write_text(json.dumps({"fundamentals": fundamentals}))
    return tmp_path


def test_build_forecasts_curated_and_largest_once_a_day(app, ohlcv, monkeypatch, capsys):
    monkeypatch.setattr(forecast, "TOP", 1)
    provider = FakeYahoo(ohlcv, failing={"BIG.ST"})
    forecast.build(app, provider, FakePredictor(), NOW)
    saved = json.loads((app / "data/forecasts.json").read_text())
    assert provider.asked == ["VOLV-B.ST", "BIG.ST"]  # curated + largest stock, not the fund
    assert list(saved["forecasts"]) == ["volv-b"]
    assert saved["errors"] == ["BIG.ST: Yahoo history failed for BIG.ST"]
    entry = saved["forecasts"]["volv-b"]
    assert (entry["base"], entry["baseDate"], entry["model"]) == (
        100.0,
        "2026-11-19",
        "Kronos-small",
    )
    assert saved["createdAt"] == "2026-11-20T07:30:00Z" and saved["horizon"] == forecast.HORIZON

    assert not forecast.due(app, NOW + pd.Timedelta(hours=9))
    assert forecast.due(app, NOW + pd.Timedelta(hours=forecast.MAX_AGE_HOURS))
    # A second run the same day does nothing.
    again = FakeYahoo(ohlcv)
    forecast.build(app, again, FakePredictor(), NOW + pd.Timedelta(hours=9))
    assert again.asked == [] and "aktuella" in capsys.readouterr().out


def test_build_keeps_previous_file_when_nothing_works(app, ohlcv):
    (app / "data/forecasts.json").write_text(
        '{"createdAt": "2026-11-18T07:00:00Z", "forecasts": {"x": 1}}'
    )
    with pytest.raises(SystemExit, match="Ingen prognos"):
        forecast.build(
            app, FakeYahoo(ohlcv, failing={"VOLV-B.ST", "BIG.ST", "SMALL.ST"}), FakePredictor(), NOW
        )
    assert json.loads((app / "data/forecasts.json").read_text())["forecasts"] == {"x": 1}
