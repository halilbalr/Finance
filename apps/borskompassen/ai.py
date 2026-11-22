"""Gemini skriver dagens omvärldsanalys (data/briefs.json) och korta aktieanalyser (data/analyses.json).

Kör efter details.py, från repots rot, med en gratis nyckel från Google AI Studio i
GEMINI_API_KEY:
    python apps/borskompassen/ai.py

Utan nyckel görs ingenting. Analysen bygger bara på dagens rubriker i data/headlines.json; källorna
i appen kommer från rubrikerna, aldrig från modellen. Branscher, riktningar och längder kontrolleras
innan något sparas. Körs högst en gång per dag; aktieanalyserna läggs också in i detaljfilerna.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))

from details import _largest  # noqa: E402

MODELS = [os.environ.get("GEMINI_MODEL") or "gemini-flash-latest", "gemini-2.5-flash"]
ANALYSES_TOP = 40  # de största aktierna som får en AI-analys, utöver de handplockade
MAX_BRIEFS, PAUSE = 30, 7  # sparade analyser; sekunder mellan anrop (gratisnivåns takt)
SECTORS = [
    "Bank", "Industri", "Försvar", "Teknik", "Handel", "Läkemedel", "Gruvor & metaller",
    "Energi", "Spel", "Investmentbolag", "Fastigheter", "Globalfond", "Sverigefond", "Räntor",
]  # fmt: skip
MOODS = {"positiv": "Positiv stämning", "negativ": "Negativ stämning", "forsiktig": "Försiktig stämning",
         "neutral": "Blandad stämning"}  # fmt: skip
SOURCE_NOTE = (
    "Skriven automatiskt av AI (Google Gemini) utifrån dagens rubriker från SVT, DI, Riksbanken, "
    "BBC och CNBC. Den kan innehålla fel och är inget råd. Läs källorna."
)


def gemini(prompt, key, timeout=120):
    """Svar från Gemini som JSON. Provar MODELS i tur och ordning; väntar en gång vid strypning."""
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3},
    }).encode()  # fmt: skip
    last = None
    for model in MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in (1, 2):
            request = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json", "x-goog-api-key": key}
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    payload = json.loads(response.read())
                parts = payload["candidates"][0]["content"]["parts"]
                return json.loads("".join(p.get("text", "") for p in parts)), model
            except urllib.error.HTTPError as exc:
                last = f"{model}: HTTP {exc.code}"
                if exc.code == 429 and attempt == 1:
                    time.sleep(60)
                    continue
                break  # 404: modellen finns inte, prova nästa
            except (KeyError, IndexError, ValueError, OSError) as exc:
                last = f"{model}: {exc}"
                break
    raise RuntimeError(f"Gemini svarade inte: {last}")


def _text(value, limit):
    return re.sub(r"\s+", " ", value).strip()[:limit] if isinstance(value, str) else ""


def brief_prompt(headlines):
    lines = "\n".join(f"[{n}] {h['source']}: {h['title']}" for n, h in enumerate(headlines))
    return f"""Du skriver dagens omvärldsanalys i Börskompassen, en svensk app för småsparare utan förkunskaper.
Utgå BARA från rubrikerna nedan. Hitta inte på händelser, siffror eller citat som inte står där.
Är något oklart, skriv försiktigt ("rubriken tyder på"). Ge inga köp- eller säljråd.
Välj de 3-5 händelser som betyder mest för aktier och fonder. Enkel svenska, korta meningar.

Svara med JSON:
{{"title": "rubrik för dagen, högst 90 tecken",
 "mood": "positiv" | "negativ" | "forsiktig" | "neutral",
 "summary": "2-3 meningar om läget",
 "items": [{{"title": "...", "category": "t.ex. Räntor, Teknik, Råvaror & geopolitik",
   "region": "t.ex. Sverige, USA, Världen", "importance": 1-3,
   "what": "vad hände, 1-2 meningar", "why": "varför det kan påverka aktier, 1-2 meningar",
   "chain": ["3-5 korta steg från händelse till aktiekurs"],
   "impacts": [{{"sector": en av {SECTORS}, "dir": 1 (gynnas) eller -1 (missgynnas), "strength": 1-3, "why": "en mening"}}],
   "sources": [nummer på rubrikerna som händelsen bygger på]}}]}}

Rubriker:
{lines}"""


def make_brief(headlines, answer, today, model):
    """Kontrollerad analys i appens briefs-format, eller ValueError."""
    items = []
    for n, raw in enumerate(answer.get("items") or []):
        if (
            not isinstance(raw, dict)
            or not _text(raw.get("title"), 1)
            or not _text(raw.get("what"), 1)
        ):
            continue
        refs = [
            s for s in raw.get("sources") or [] if isinstance(s, int) and 0 <= s < len(headlines)
        ]
        if not refs:
            continue  # en händelse utan rubrik att peka på kan vara påhittad
        impacts = []
        for im in raw.get("impacts") or []:
            if isinstance(im, dict) and im.get("sector") in SECTORS and im.get("dir") in (-1, 1):
                strength = im.get("strength") if im.get("strength") in (1, 2, 3) else 1
                impacts.append({"sector": im["sector"], "dir": im["dir"], "strength": strength,
                                "why": _text(im.get("why"), 200)})  # fmt: skip
        items.append({
            "id": f"{today}-{n}", "title": _text(raw["title"], 120),
            "category": _text(raw.get("category"), 40), "region": _text(raw.get("region"), 40),
            "importance": raw.get("importance") if raw.get("importance") in (1, 2, 3) else 2,
            "what": _text(raw["what"], 400), "why": _text(raw.get("why"), 400),
            "chain": [_text(s, 80) for s in (raw.get("chain") or []) if _text(s, 80)][:6],
            "impacts": impacts[:6],
            "sources": [{"title": headlines[s]["title"], "url": headlines[s]["url"]}
                        for s in dict.fromkeys(refs)][:4],
        })  # fmt: skip
    if not items:
        raise ValueError("Gemini gav ingen händelse med källor")
    mood = answer.get("mood") if answer.get("mood") in MOODS else "neutral"
    return {
        "date": today, "title": _text(answer.get("title"), 120) or "Dagens läge",
        "mood": mood, "moodText": MOODS[mood], "summary": _text(answer.get("summary"), 600),
        "sourceNote": SOURCE_NOTE, "items": items[:5],
        "updatedAt": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
        "author": f"Gemini ({model})",
    }  # fmt: skip


def analysis_prompt(quote, detail, brief):
    signals = "; ".join(f"{s['title']}: {s['text']}" for s in detail.get("technical", []))
    news = "\n".join(f"- {n['title']}" for n in detail.get("news", [])[:6]) or "- (inga)"
    world = (
        "; ".join(
            f"{it['title']} ({'gynnar' if im['dir'] > 0 else 'missgynnar'} {im['sector']})"
            for it in (brief or {}).get("items", [])
            for im in it["impacts"]
            if im["sector"] == quote.get("sector")
        )
        or "inget som direkt berör branschen"
    )
    return f"""Skriv en kort analys av {quote["name"]} ({quote.get("ticker")}) för en svensk småsparare utan förkunskaper.
Använd BARA fakta nedan. Hitta inte på siffror eller händelser. Ge inget köp- eller säljråd.
Svara med JSON: {{"summary": "3-4 meningar", "plus": ["högst 3 korta styrkor"], "minus": ["högst 3 korta risker"]}}

Bransch: {quote.get("sector")}. Beskrivning: {quote.get("about") or "-"}
Kurs: {quote["price"]} {quote.get("currency")} ({quote.get("date")}). Utveckling: {json.dumps(detail.get("changes", {}), ensure_ascii=False)}
Nyckeltal: {json.dumps(detail.get("keyFigures", {}), ensure_ascii=False)}
Tekniska signaler: {signals or "-"}
Omvärlden i dag: {world}
Senaste nyheterna (engelska rubriker från Yahoo):
{news}"""


def make_analysis(answer, model, stamp):
    summary = _text(answer.get("summary"), 700)
    if not summary:
        raise ValueError("tom analys")
    pick = lambda key: [_text(s, 160) for s in (answer.get(key) or []) if _text(s, 160)][:3]  # noqa: E731
    return {"summary": summary, "plus": pick("plus"), "minus": pick("minus"),
            "model": model, "createdAt": stamp}  # fmt: skip


def build(app, ask, now, top=None):
    """ask(prompt) -> (svar som dict, modellnamn)."""
    data = app / "data"
    today, stamp = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H:%M:%SZ")
    briefs = _read(data / "briefs.json").get("briefs", [])
    if not any(
        b.get("date") == today and str(b.get("author", "")).startswith("Gemini") for b in briefs
    ):
        heads = _read(data / "headlines.json").get("headlines", [])[:40]
        if heads:
            try:
                answer, model = ask(brief_prompt(heads))
                brief = make_brief(heads, answer, today, model)
                briefs = [brief] + [b for b in briefs if b.get("date") != today]
                _write(data / "briefs.json", {"briefs": briefs[:MAX_BRIEFS]}, indent=1)
                print(f"ok   omvärldsanalys: {brief['title']}")
            except (RuntimeError, ValueError) as exc:
                print(f"fel  omvärldsanalys: {exc}", file=sys.stderr)
    latest = next((b for b in briefs if b.get("date") == today), briefs[0] if briefs else None)

    stored = _read(data / "analyses.json")
    analyses = stored.get("analyses", {}) if stored.get("date") == today else {}
    quotes = {q["id"]: q for q in json.loads((data / "quotes.json").read_text("utf-8"))["quotes"]}
    fundamentals = _read(app / "universe.json").get("fundamentals", {})
    curated = [
        i["id"] for i in json.loads((app / "instruments.json").read_text("utf-8"))["instruments"]
    ]
    stocks = [q for q in quotes.values() if q.get("type") == "aktie"]
    chosen = _largest(stocks, fundamentals, _read(data / "meta.json").get("fx", {}), curated,
                      ANALYSES_TOP if top is None else top)  # fmt: skip
    made = 0
    for id in chosen:
        path = data / "detail" / f"{id}.json"
        if id in analyses or id not in quotes or not path.exists():
            continue
        detail = _read(path)
        try:
            answer, model = ask(analysis_prompt(quotes[id], detail, latest))
            analyses[id] = make_analysis(answer, model, stamp)
            made += 1
        except (RuntimeError, ValueError) as exc:
            print(f"fel  analys {id}: {exc}", file=sys.stderr)
            continue
        time.sleep(PAUSE)
    for id, entry in analyses.items():  # lägg in i detaljfilerna, som byggs om varje körning
        path = data / "detail" / f"{id}.json"
        if path.exists():
            _write(path, {**_read(path), "analysis": entry})
    _write(data / "analyses.json", {"date": today, "analyses": analyses})
    print(f"Klart: {made} nya aktieanalyser, {len(analyses)} för i dag.")


def _read(path):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _write(path, payload, indent=None):
    separators = None if indent else (",", ":")
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=indent, separators=separators), "utf-8"
    )


if __name__ == "__main__":
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("Ingen GEMINI_API_KEY; hoppar över AI-analyserna.")
    else:
        build(APP, lambda prompt: gemini(prompt, key), pd.Timestamp.now(tz="UTC"))
