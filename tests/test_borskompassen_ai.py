"""Börskompassen's Gemini brief and analyses, with fake answers; no key or network required."""

import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pandas as pd
import pytest

SPEC = importlib.util.spec_from_file_location(
    "ai", Path(__file__).resolve().parents[1] / "apps/borskompassen/ai.py"
)
ai = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ai)

NOW = pd.Timestamp("2026-11-21 07:30", tz="UTC")
HEADLINES = [
    {"source": "SVT Ekonomi", "title": "Börsras på Wall Street", "url": "https://svt.se/1"},
    {"source": "Dagens industri", "title": "Oljepriset stiger", "url": "https://di.se/2"},
]
ANSWER = {
    "title": "Oro på börserna", "mood": "orolig",  # unknown mood falls back to neutral
    "summary": "Börserna föll.",
    "items": [
        {"title": "Teknikras", "what": "Teknikaktier föll.", "why": "Oro för AI.", "chain": ["Oro", "Försäljning"],
         "impacts": [{"sector": "Teknik", "dir": -1, "strength": 3, "why": "Lägre värderingar."},
                     {"sector": "Flyg & resor", "dir": -1, "why": "okänd bransch"},
                     {"sector": "Bank", "dir": 0, "why": "ingen riktning"}],
         "sources": [0, 0, 7]},
        {"title": "Påhittad händelse", "what": "Utan källa.", "sources": []},
        {"title": "Olja", "what": "Oljan steg.", "why": "Kostnader.", "importance": 9,
         "impacts": [{"sector": "Energi", "dir": 1, "strength": 5, "why": "Mer vinst."}], "sources": [1]},
    ],
}  # fmt: skip


def test_make_brief_keeps_only_grounded_valid_content():
    brief = ai.make_brief(HEADLINES, ANSWER, "2026-11-21", "gemini-flash-latest")
    assert (brief["date"], brief["mood"], brief["moodText"]) == (
        "2026-11-21",
        "neutral",
        "Blandad stämning",
    )
    assert (
        brief["author"] == "Gemini (gemini-flash-latest)" and brief["sourceNote"] == ai.SOURCE_NOTE
    )
    tech, oil = brief["items"]  # the item without sources is dropped
    assert tech["impacts"] == [
        {"sector": "Teknik", "dir": -1, "strength": 3, "why": "Lägre värderingar."}
    ]
    assert tech["sources"] == [
        {"title": "Börsras på Wall Street", "url": "https://svt.se/1"}
    ]  # no index 7
    assert (oil["importance"], oil["impacts"][0]["strength"]) == (2, 1)  # out-of-range values reset
    with pytest.raises(ValueError, match="ingen händelse"):
        ai.make_brief(HEADLINES, {"items": [ANSWER["items"][1]]}, "2026-11-21", "m")


@pytest.fixture
def app(tmp_path):
    data = tmp_path / "data"
    (data / "detail").mkdir(parents=True)
    quotes = [{"id": i, "symbol": s, "name": n, "type": "aktie", "sector": "Teknik", "price": 100.0,
               "currency": "SEK", "date": "2026-11-20"}
              for i, s, n in [("eric-b", "ERIC-B.ST", "Ericsson"), ("big", "BIG.ST", "Big"), ("small", "SMALL.ST", "Small")]]  # fmt: skip
    (data / "quotes.json").write_text(json.dumps({"quotes": quotes}))
    (data / "meta.json").write_text(json.dumps({"fx": {"SEK": 1}}))
    (data / "headlines.json").write_text(json.dumps({"headlines": HEADLINES}))
    (data / "briefs.json").write_text(
        json.dumps({"briefs": [{"date": "2026-11-01", "author": "Claude", "items": []}]})
    )
    for q in quotes:
        (data / "detail" / f"{q['id']}.json").write_text(
            json.dumps({"id": q["id"], "technical": [], "news": []})
        )
    (tmp_path / "instruments.json").write_text(json.dumps({"instruments": [{"id": "eric-b"}]}))
    (tmp_path / "universe.json").write_text(
        json.dumps(
            {
                "fundamentals": {
                    "BIG.ST": {"price": 100, "marketCap": 9e11},
                    "SMALL.ST": {"price": 100, "marketCap": 1},
                }
            }
        )
    )
    return tmp_path


class FakeGemini:
    def __init__(self, fail_for=()):
        self.prompts, self.fail_for = [], fail_for

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if "omvärldsanalys" in prompt:
            return ANSWER, "gemini-test"
        if any(name in prompt for name in self.fail_for):
            raise RuntimeError("Gemini svarade inte: HTTP 500")
        return {
            "summary": "Stabilt bolag.",
            "plus": ["Starkt", "", "Billigt", "x", "y"],
            "minus": [],
        }, "gemini-test"


def test_build_writes_brief_and_analyses_once_a_day(app, monkeypatch):
    monkeypatch.setattr(ai, "PAUSE", 0)
    ask = FakeGemini(fail_for=["Big"])
    ai.build(app, ask, NOW, top=1)
    briefs = json.loads((app / "data/briefs.json").read_text())["briefs"]
    assert [b["date"] for b in briefs] == ["2026-11-21", "2026-11-01"]  # newest first, old kept
    assert "Börsras på Wall Street" in ask.prompts[0]

    analyses = json.loads((app / "data/analyses.json").read_text())
    assert analyses["updatedAt"] == "2026-11-21T07:30:00Z" and list(analyses["analyses"]) == [
        "eric-b"
    ]  # Big failed
    entry = analyses["analyses"]["eric-b"]
    assert entry == {"summary": "Stabilt bolag.", "plus": ["Starkt", "Billigt", "x"], "minus": [],
                     "model": "gemini-test", "createdAt": "2026-11-21T07:30:00Z"}  # fmt: skip
    assert json.loads((app / "data/detail/eric-b.json").read_text())["analysis"] == entry
    assert "Teknikras (missgynnar Teknik)" in ask.prompts[1]  # today's brief informs the analysis

    # Later the same day: no new brief, only the missing analysis is retried.
    again = FakeGemini()
    ai.build(app, again, NOW + pd.Timedelta(hours=9), top=1)
    assert len(again.prompts) == 1 and "Big" in again.prompts[0]
    assert len(json.loads((app / "data/briefs.json").read_text())["briefs"]) == 2


def test_analyses_last_a_week_and_the_oldest_are_rewritten_first(app, monkeypatch):
    monkeypatch.setattr(ai, "PAUSE", 0)
    stamp = lambda days: (NOW - pd.Timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    old = {"summary": "Gammal.", "plus": [], "minus": [], "model": "m"}
    (app / "data/analyses.json").write_text(json.dumps({"analyses": {
        "eric-b": {**old, "createdAt": stamp(1)}, "big": {**old, "createdAt": stamp(3)},
        "small": {**old, "createdAt": stamp(8)},  # older than a week: dropped
    }}))  # fmt: skip

    class OneLeft(FakeGemini):  # the daily quota runs out after one stock
        def __call__(self, prompt):
            if "omvärldsanalys" not in prompt and len(self.prompts) > 1:
                raise ai.QuotaExhausted("dagens gratiskvot hos Gemini är slut")
            return super().__call__(prompt)

    ask = OneLeft()
    ai.build(app, ask, NOW, top=1)
    assert len(ask.prompts) == 2 and "Big" in ask.prompts[1]  # oldest first, then stop
    saved = json.loads((app / "data/analyses.json").read_text())["analyses"]
    assert set(saved) == {"eric-b", "big"} and saved["big"]["createdAt"] == stamp(0)
    assert saved["eric-b"]["summary"] == "Gammal."  # kept until a later run has quota
    detail = json.loads((app / "data/detail/eric-b.json").read_text())
    assert detail["analysis"]["summary"] == "Gammal."


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_gemini_tries_next_model_and_waits_when_rate_limited(monkeypatch):
    calls, waits = [], []
    answer = {"candidates": [{"content": {"parts": [{"text": '{"summary": "ok"}'}]}}]}

    def urlopen(request, timeout):
        calls.append(request.full_url.split("/models/")[1])
        assert request.get_header("X-goog-api-key") == "nyckel"
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, None)
        if len(calls) == 2:
            raise urllib.error.HTTPError(request.full_url, 429, "slow down", {}, None)
        return Response(json.dumps(answer).encode())

    monkeypatch.setattr(ai.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(ai.time, "sleep", waits.append)
    monkeypatch.setattr(ai, "MODELS", ["saknas", "gemini-flash-lite-latest"])
    monkeypatch.setattr(ai, "SPENT", set())
    assert ai.gemini("hej", "nyckel") == ({"summary": "ok"}, "gemini-flash-lite-latest")
    assert calls == [
        "saknas:generateContent",
        "gemini-flash-lite-latest:generateContent",
        "gemini-flash-lite-latest:generateContent",
    ]
    assert waits == [60]


def test_gemini_skips_models_whose_daily_quota_is_spent(monkeypatch):
    calls, waits = [], []
    answer = {"candidates": [{"content": {"parts": [{"text": '{"summary": "ok"}'}]}}]}
    per_day = b'{"error": {"details": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}}'
    lite_left = [True]

    def urlopen(request, timeout):
        model = request.full_url.split("/models/")[1].split(":")[0]
        calls.append(model)
        if model == "flash" or not lite_left[0]:
            raise urllib.error.HTTPError(request.full_url, 429, "quota", {}, io.BytesIO(per_day))
        return Response(json.dumps(answer).encode())

    monkeypatch.setattr(ai.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(ai.time, "sleep", waits.append)
    monkeypatch.setattr(ai, "MODELS", ["flash", "lite"])
    monkeypatch.setattr(ai, "SPENT", set())
    assert ai.gemini("a", "k") == ({"summary": "ok"}, "lite")
    assert ai.gemini("b", "k") == ({"summary": "ok"}, "lite")
    assert calls == ["flash", "lite", "lite"] and waits == []  # no waiting, flash not asked again
    lite_left[0] = False
    with pytest.raises(ai.QuotaExhausted):
        ai.gemini("c", "k")
