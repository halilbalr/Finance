"""Börskompassen's universe: which Yahoo instruments are followed and how they are labelled."""

import importlib.util
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

SPEC = importlib.util.spec_from_file_location(
    "build_universe",
    Path(__file__).resolve().parents[1] / "apps/borskompassen/build_universe.py",
)
bu = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bu)

TODAY = date(2026, 11, 2)
RECENT = int(datetime(2026, 10, 30, 17).timestamp())
OLD = int(datetime(2026, 9, 1, 17).timestamp())
CONFIG = {
    "fx": {"USD": "USDSEK=X", "NOK": "NOKSEK=X", "EUR": "EURSEK=X"},
    "instruments": [
        {"id": "volv-b", "symbol": "VOLV-B.ST"},
        {"id": "aapl", "symbol": "AAPL"},
        {"id": "lato-b-st", "symbol": "XYZ"},  # a curated id an automatic one would take
    ],
}
INDUSTRIES = {"Industrials": ["Conglomerates"], "Financial Services": ["Banks—Regional"]}


def row(symbol, **fields):
    return {
        "symbol": symbol,
        "longName": f"{symbol} AB",
        "currency": "SEK",
        "marketCap": 1e9,
        "regularMarketTime": RECENT,
        "regularMarketPrice": 100.0,
        **fields,
    }


def fund(name, assets, **fields):
    return row(f"0P{abs(hash(name)) % 10**8:08d}.ST", longName=name, netAssets=assets,
               quoteType="MUTUALFUND", marketCap=None, **fields)  # fmt: skip


FUNDS = [
    fund("Handelsbanken Långränta (B1 SEK)", 5e9),
    fund("Handelsbanken Långränta (A1 SEK)", 9e9),
    fund("Avanza Global", 60e9),
    fund("Gammal Fond A", 80e9, regularMarketTime=OLD),
]
SCREENS = {
    ("equity", "STO"): [
        row("VOLV-B.ST", trailingPE=16.2, dividendYield=2.74, regularMarketPrice=309.0),
        row("LATO-B.ST", longName="Investment AB Latour"),
        row("SHB-A.ST", dividendYield=5.14, trailingAnnualDividendRate=999),
        row("ALZ-TO5.ST", marketCap=None),  # warrant
        row("GBP.ST", currency="GBP"),
        row("QUIET.ST", regularMarketTime=OLD),
    ],
    ("industry", "STO", "Conglomerates"): [{"symbol": "LATO-B.ST"}],
    ("industry", "STO", "Banks—Regional"): [{"symbol": "SHB-A.ST"}],
    ("equity", "OSL"): [row("EQNR.OL", currency="NOK")],
    ("us",): [row("AAPL", currency="USD", trailingPE=30.0), row("MSFT", currency="USD")],
    ("fund", "STO"): FUNDS,
    ("etf", "STO"): [row("XACT-OMXS30.ST", longName="XACT OMXS30 ESG", marketCap=None),
                     row("2LBT.ST", longName=None, marketCap=None)],
}  # fmt: skip
SP500 = pd.DataFrame(
    {"name": ["Apple Inc.", "Microsoft"], "sector": ["Information Technology"] * 2},
    index=["AAPL", "MSFT"],
)


def build(previous=None, screens=SCREENS):
    return bu.build(
        CONFIG, previous or {}, lambda spec: screens.get(spec, []), lambda: SP500, INDUSTRIES, TODAY
    )


def by_symbol(universe):
    return {i["symbol"]: i for i in universe["instruments"]}


def test_selection_and_labels():
    universe = build()
    found = by_symbol(universe)
    for left_out in ["VOLV-B.ST", "AAPL", "ALZ-TO5.ST", "GBP.ST", "QUIET.ST", "2LBT.ST"]:
        assert left_out not in found  # curated, warrant, unknown currency, inactive, unnamed
    assert found["LATO-B.ST"] == {
        "id": "lato-b-st-2",  # the plain id belongs to a curated instrument
        "symbol": "LATO-B.ST",
        "ticker": "LATO B",
        "name": "Investment AB Latour",
        "type": "aktie",
        "sector": "Investmentbolag",
        "about": "Noterat på Stockholmsbörsen. Bransch enligt Yahoo Finance: Conglomerates.",
    }
    assert found["SHB-A.ST"]["sector"] == "Bank"
    assert found["EQNR.OL"]["about"] == "Noterat på Oslobörsen."
    assert found["EQNR.OL"]["sector"] == "Annat"  # no industry answer for Oslo here
    assert (found["MSFT"]["sector"], found["MSFT"]["name"]) == ("Teknik", "MSFT AB")
    assert found["XACT-OMXS30.ST"]["sector"] == "Sverigefond"
    assert found["^GSPC"]["id"] == "idx-sp500" and found["GC=F"]["sector"] == "Gruvor & metaller"
    assert len({i["id"] for i in universe["instruments"]}) == len(universe["instruments"])


def test_funds_keep_one_share_class_and_skip_inactive():
    found = by_symbol(build())
    names = sorted(i["name"] for i in found.values() if i["type"] == "fond" and "0P" in i["symbol"])
    assert names == ["Avanza Global", "Handelsbanken Långränta (A1 SEK)"]  # largest class kept
    long_rate = next(i for i in found.values() if i["name"].startswith("Handelsbanken"))
    assert long_rate["sector"] == "Räntor"
    assert long_rate["about"] == "Fond som handlas i Stockholm. Förvaltar cirka 9 miljarder SEK."


def test_fundamentals_cover_curated_symbols_and_use_yahoo_yield():
    fundamentals = build()["fundamentals"]
    assert fundamentals["VOLV-B.ST"] == {"pe": 16.2, "dividendYield": 2.74, "price": 309.0}
    assert fundamentals["AAPL"]["pe"] == 30.0
    # dividendYield, not trailingAnnualDividendRate, which may be in another currency
    assert fundamentals["SHB-A.ST"]["dividendYield"] == 5.14
    assert "ALZ-TO5.ST" not in fundamentals and "QUIET.ST" not in fundamentals


def test_vanished_instruments_keep_ids_for_a_grace_period():
    previous = {
        "instruments": [
            {"id": "ny-gammal", "symbol": "SHB-A.ST", "name": "old"},  # still listed
            {"id": "borta", "symbol": "GONE.HE"},
            {"id": "lang-borta", "symbol": "OLD.HE", "missingSince": "2026-09-01"},
            {"id": "shb-a-st", "symbol": "PREV.ST", "missingSince": "2026-10-20"},
        ]
    }
    screens = {**SCREENS, ("equity", "STO"): SCREENS[("equity", "STO")] + [row("SHB-A-ST")]}
    found = by_symbol(build(previous, screens))
    assert found["SHB-A.ST"]["id"] == "ny-gammal"  # known symbols keep their id
    assert found["GONE.HE"] == {"id": "borta", "symbol": "GONE.HE", "missingSince": "2026-11-02"}
    assert found["PREV.ST"]["missingSince"] == "2026-10-20"
    assert "OLD.HE" not in found  # missing for more than 30 days
    assert found["SHB-A-ST"]["id"] == "shb-a-st-2"  # a newcomer never takes a known id


@pytest.mark.parametrize(
    "name,base,sector",
    [
        ("Handelsbanken Långränta (A10 SEK)", "handelsbanken långränta", "Räntor"),
        ("Storebrand Kortränta A SEK H", "storebrand kortränta", "Räntor"),
        ("Danske Invest Sverige Kort Ränta WA d", "danske invest sverige kort ränta", "Räntor"),
        ("Dynamic R5 - A", "dynamic r5", "Annat"),
        (
            "Spiltan Aktiefond Investmentbolag",
            "spiltan aktiefond investmentbolag",
            "Investmentbolag",
        ),
        ("SEB Teknologifond D (SEK)", "seb teknologifond", "Teknik"),
        ("Avanza Zero", "avanza zero", "Sverigefond"),
        ("Länsförsäkringar USA Index", "länsförsäkringar usa index", "Globalfond"),
        ("Handelsbanken Hållbar Glob Obl (A1 SEK)", "handelsbanken hållbar glob obl", "Räntor"),
        ("Swedbank Robur Transfer 70", "swedbank robur transfer 70", "Annat"),
    ],
)
def test_fund_names(name, base, sector):
    assert (bu.fund_base(name), bu.fund_sector(name)) == (base, sector)


@pytest.mark.parametrize(
    "sector,industry,symbol,expected",
    [
        ("Industrials", "Aerospace & Defense", "SAAB-B.ST", "Försvar"),
        ("Industrials", "Conglomerates", "LUND-B.ST", "Investmentbolag"),
        ("Industrials", "Conglomerates", "MMM", "Industri"),
        ("Financial Services", "Asset Management", "INVE-B.ST", "Investmentbolag"),
        ("Financial Services", "Insurance—Life", "X.ST", "Bank"),
        ("Consumer Cyclical", "Gambling", "EVO.ST", "Spel"),
        ("Consumer Cyclical", "Auto Manufacturers", "VOLCAR-B.ST", "Industri"),
        ("Basic Materials", "Other Industrial Metals & Mining", "BOL.ST", "Gruvor & metaller"),
        ("Basic Materials", "Paper & Paper Products", "HOLM-B.ST", "Industri"),
        ("Communication Services", "Telecom Services", "TEL2-B.ST", "Teknik"),
        (None, None, "NEW.ST", "Annat"),
    ],
)
def test_app_sector(sector, industry, symbol, expected):
    assert bu.app_sector(sector, industry, symbol) == expected
