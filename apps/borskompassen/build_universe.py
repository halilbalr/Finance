"""Bygger universe.json: alla aktier, fonder och annat Börskompassen följer utöver instruments.json.

Kör från repots rot, med paketet installerat med data-tillägget:
    python apps/borskompassen/build_universe.py              # bygg alltid
    python apps/borskompassen/build_universe.py --max-age-days 7   # bara om listan är äldre

Urvalet:
- aktier med börsvärde på Stockholms-, Oslo-, Helsingfors- och Köpenhamnsbörsen (Yahoos screener;
  teckningsoptioner, certifikat och liknande saknar börsvärde och tas inte med)
- S&P 500 (repots sp500_constituents)
- de största svenska fonderna efter förvaltat kapital, en andelsklass per fond
- börshandlade fonder i Stockholm, index, råvaror, kryptovalutor och valutor

instruments.json går först: dess id:n och texter ändras aldrig härifrån. Ett instrument som
försvinner ur Yahoos listor ligger kvar i 30 dagar (märkt missingSince), så att sparade affärer
inte tappar sin aktie vid ett tillfälligt avbrott. P/E och direktavkastning sparas tillsammans
med kursen de gäller för; update_data.py räknar om dem mot dagens kurs.
"""

import argparse
import json
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

APP = Path(__file__).resolve().parent
FUNDS = 300
GRACE_DAYS = 30
EXCHANGES = {  # Yahoos börskod -> (symbolsuffix, namn i appen)
    "STO": ("ST", "Stockholmsbörsen"),
    "OSL": ("OL", "Oslobörsen"),
    "HEL": ("HE", "Helsingforsbörsen"),
    "CPH": ("CO", "Köpenhamnsbörsen"),
}
MINING = {
    "Aluminum", "Coking Coal", "Copper", "Gold", "Other Industrial Metals & Mining",
    "Other Precious Metals & Mining", "Silver", "Steel",
}  # fmt: skip
INDUSTRY_SECTOR = {  # Yahoos industri -> appens bransch, före sektorregeln nedan
    "Banks—Diversified": "Bank", "Banks—Regional": "Bank", "Asset Management": "Investmentbolag",
    "Aerospace & Defense": "Försvar", "Gambling": "Spel", "Resorts & Casinos": "Spel",
    "Electronic Gaming & Multimedia": "Spel", "Auto Manufacturers": "Industri",
    "Auto Parts": "Industri", "Recreational Vehicles": "Industri", "Shell Companies": "Annat",
    **{name: "Gruvor & metaller" for name in MINING},
}  # fmt: skip
SECTOR = {  # Yahoos sektor -> appens bransch
    "Financial Services": "Bank", "Industrials": "Industri", "Consumer Cyclical": "Handel",
    "Consumer Defensive": "Handel", "Communication Services": "Teknik", "Technology": "Teknik",
    "Healthcare": "Läkemedel", "Basic Materials": "Industri", "Energy": "Energi",
    "Utilities": "Energi", "Real Estate": "Fastigheter",
}  # fmt: skip
GICS = {  # S&P 500-sektor (GICS) -> appens bransch
    "Information Technology": "Teknik", "Communication Services": "Teknik",
    "Health Care": "Läkemedel", "Financials": "Bank", "Industrials": "Industri",
    "Consumer Discretionary": "Handel", "Consumer Staples": "Handel", "Energy": "Energi",
    "Utilities": "Energi", "Materials": "Industri", "Real Estate": "Fastigheter",
}  # fmt: skip
FUND_WORDS = [  # första träffen i fondens namn avgör bransch
    ("Räntor", ["ränte", "ränta", "oblig", " obl", "bond", " frn", "fixed inc", "likviditet", "penning",
                "kredit", "credit"]),
    ("Investmentbolag", ["investmentbolag"]),
    ("Fastigheter", ["fastighet", "real estate"]),
    ("Läkemedel", ["läkemedel", "hälsa", "health", "bioteknik", "pharma"]),
    ("Teknik", ["teknik", "teknologi", "technology"]),
    ("Sverigefond", ["sverige", "sweden", "svensk", "swedish", "omxs30", "avanza zero", "småbolag",
                     "allemansfond", "aktiesparfond", "exportfond"]),
    ("Globalfond", ["global", "glob ", "världen", "world", "usa", "nordamerika", "europa",
                    "emerging", "tillväxtmarknad", "asien", "japan", "kina", "indien"]),
]  # fmt: skip
# Andelsklass i slutet av namnet: "(A10 SEK)", en eller två versaler ("A", "WA"), "d" för
# utdelande, en valuta eller "Acc".
FUND_CLASS = re.compile(r"\s*\([^)]*\)$|\s+(?:-|[A-Z]{1,2}|d|SEK|EUR|USD|NOK|DKK|Acc|Utd|Dis)$")


def _extra(id, symbol, ticker, name, sector, about):
    return {"id": id, "symbol": symbol, "ticker": ticker, "name": name, "type": "fond",
            "sector": sector, "about": about}  # fmt: skip


EXTRAS = [
    _extra("idx-omxspi", "^OMXSPI", "OMXSPI", "Stockholmsbörsen (OMXSPI, index)", "Sverigefond",
           "Alla aktier på Stockholmsbörsen, viktade efter storlek. Visar hur hela den svenska börsen går."),
    _extra("idx-sp500", "^GSPC", "S&P 500", "S&P 500 (index)", "Globalfond",
           "De 500 största bolagen i USA. Världens mest följda börsindex."),
    _extra("idx-nasdaq100", "^NDX", "NDX", "Nasdaq 100 (index)", "Globalfond",
           "De 100 största bolagen på Nasdaq-börsen i USA, främst teknikbolag."),
    _extra("idx-nasdaq", "^IXIC", "IXIC", "Nasdaq Composite (index)", "Globalfond",
           "Alla bolag på Nasdaq-börsen i USA. Domineras av teknikbolag."),
    _extra("idx-dowjones", "^DJI", "DJI", "Dow Jones (index)", "Globalfond",
           "30 stora amerikanska bolag. Ett av världens äldsta börsindex."),
    _extra("idx-eurostoxx50", "^STOXX50E", "SX5E", "Euro Stoxx 50 (index)", "Globalfond",
           "50 stora bolag i euroländerna."),
    _extra("idx-dax", "^GDAXI", "DAX", "DAX (index)", "Globalfond",
           "De 40 största bolagen på Frankfurtbörsen i Tyskland."),
    _extra("idx-ftse100", "^FTSE", "FTSE", "FTSE 100 (index)", "Globalfond",
           "De 100 största bolagen på Londonbörsen."),
    _extra("idx-nikkei", "^N225", "N225", "Nikkei 225 (index)", "Globalfond",
           "225 stora bolag på Tokyobörsen i Japan."),
    _extra("idx-hangseng", "^HSI", "HSI", "Hang Seng (index)", "Globalfond",
           "De största bolagen på Hongkongbörsen, många med verksamhet i Kina."),
    _extra("idx-vix", "^VIX", "VIX", "VIX, rädsloindex", "Annat",
           "Mäter hur stora kurssvängningar placerare väntar sig i USA den närmaste månaden. Högt värde betyder oro."),
    _extra("ravara-olja-brent", "BZ=F", "BRENT", "Olja (Brent)", "Energi",
           "Priset på ett fat Nordsjöolja i dollar. Påverkar bensinpriset, inflationen och oljebolagen."),
    _extra("ravara-olja-wti", "CL=F", "WTI", "Olja (WTI)", "Energi",
           "Priset på ett fat amerikansk olja i dollar."),
    _extra("ravara-naturgas", "NG=F", "NATGAS", "Naturgas (USA)", "Energi",
           "Priset på amerikansk naturgas i dollar."),
    _extra("ravara-guld", "GC=F", "GULD", "Guld", "Gruvor & metaller",
           "Priset på ett uns guld i dollar. Guld brukar stiga när oron i världen ökar."),
    _extra("ravara-silver", "SI=F", "SILVER", "Silver", "Gruvor & metaller",
           "Priset på ett uns silver i dollar."),
    _extra("ravara-koppar", "HG=F", "KOPPAR", "Koppar", "Gruvor & metaller",
           "Priset på koppar i dollar. Används i el och bygg, så priset följer konjunkturen."),
    _extra("ravara-platina", "PL=F", "PLATINA", "Platina", "Gruvor & metaller",
           "Priset på ett uns platina i dollar."),
    _extra("ravara-vete", "ZW=F", "VETE", "Vete", "Annat",
           "Priset på vete i amerikanska cent per bushel (ungefär 27 kg)."),
    _extra("krypto-bitcoin", "BTC-USD", "BTC", "Bitcoin", "Annat",
           "Den största kryptovalutan. Priset i dollar svänger mycket och handlas dygnet runt."),
    _extra("krypto-ethereum", "ETH-USD", "ETH", "Ethereum", "Annat",
           "Den näst största kryptovalutan. Priset i dollar svänger mycket."),
    _extra("krypto-solana", "SOL-USD", "SOL", "Solana", "Annat",
           "Kryptovaluta. Priset i dollar svänger mycket."),
    _extra("krypto-xrp", "XRP-USD", "XRP", "XRP", "Annat",
           "Kryptovaluta. Priset i dollar svänger mycket."),
    _extra("valuta-usd", "USDSEK=X", "USD/SEK", "Dollar i kronor", "Annat",
           "Hur många kronor en amerikansk dollar kostar."),
    _extra("valuta-eur", "EURSEK=X", "EUR/SEK", "Euro i kronor", "Annat",
           "Hur många kronor en euro kostar."),
    _extra("valuta-nok", "NOKSEK=X", "NOK/SEK", "Norska kronor i svenska", "Annat",
           "Hur många svenska kronor en norsk krona kostar."),
    _extra("valuta-gbp", "GBPSEK=X", "GBP/SEK", "Pund i kronor", "Annat",
           "Hur många kronor ett brittiskt pund kostar."),
]  # fmt: skip


def slug(symbol):
    return re.sub(r"[^a-z0-9]+", "-", symbol.lower()).strip("-")


def app_sector(sector, industry, symbol=""):
    if industry == "Conglomerates" and symbol.endswith(".ST"):
        return "Investmentbolag"  # Latour, Lundbergs och liknande
    return INDUSTRY_SECTOR.get(industry) or SECTOR.get(sector) or "Annat"


def fund_sector(name):
    lower = name.lower()
    return next((s for s, words in FUND_WORDS if any(w in lower for w in words)), "Annat")


def _active(row, today, days=14):
    stamp = row.get("regularMarketTime")
    return bool(stamp) and date.fromtimestamp(stamp) >= today - timedelta(days=days)


def _fundamentals(row):
    """P/E och direktavkastning (procent) vid kursen i samma rad, eller None."""
    price = row.get("regularMarketPrice")
    if not price:
        return None
    # dividendYield är redan i procent och i handelsvalutan; trailingAnnualDividendRate kan vara
    # i rapportvalutan (AstraZeneca i dollar mot kurs i kronor) och ger då fel avkastning.
    return {"pe": row.get("trailingPE"), "dividendYield": row.get("dividendYield"), "price": price}


def fund_base(name):
    """Fondens namn utan andelsklass: 'Handelsbanken Långränta (A10 SEK)' -> 'handelsbanken långränta'."""
    previous = None
    while previous != name:
        previous, name = name, FUND_CLASS.sub("", name).strip()
    return name.lower()


def build(config, previous, screen, sp500, industries, today):
    """Nytt universum. screen((typ, ...)) ger Yahoos screenerrader; sp500() repots S&P 500-tabell."""
    allowed = {"SEK", *config.get("fx", {})}
    curated = config["instruments"]
    taken_symbols = {i["symbol"] for i in curated}
    found, fundamentals = {}, {}

    def add(instrument):
        if instrument["symbol"] not in taken_symbols and instrument["symbol"] not in found:
            found[instrument["symbol"]] = instrument

    for exchange, (suffix, venue) in EXCHANGES.items():
        rows = screen(("equity", exchange))
        if not rows:
            continue  # Yahoo svarade inte: tidigare instrument ligger kvar under karenstiden
        classes = {}
        for sector, names in industries.items():
            for industry in names:
                for row in screen(("industry", exchange, industry)):
                    classes[row["symbol"]] = (sector, industry)
        for row in rows:
            symbol = row["symbol"]
            if not (
                row.get("marketCap") and row.get("currency") in allowed and _active(row, today)
            ):
                continue
            fundamentals[symbol] = _fundamentals(row)
            sector, industry = classes.get(symbol, (None, None))
            about = f"Noterat på {venue}." + (
                f" Bransch enligt Yahoo Finance: {industry}." if industry else ""
            )
            add({
                "id": slug(symbol), "symbol": symbol,
                "ticker": symbol.removesuffix("." + suffix).replace("-", " "),
                "name": row.get("longName") or row.get("shortName") or symbol, "type": "aktie",
                "sector": app_sector(sector, industry, symbol), "about": about,
            })  # fmt: skip

    us = {row["symbol"]: row for row in screen(("us",))}
    for symbol, member in sp500().iterrows():
        if symbol in us and us[symbol].get("regularMarketPrice"):
            fundamentals[symbol] = _fundamentals(us[symbol])
        add({
            "id": slug(symbol), "symbol": symbol, "ticker": symbol.replace("-", " "),
            "name": (us.get(symbol) or {}).get("longName") or member["name"], "type": "aktie",
            "sector": GICS.get(member["sector"], "Annat"),
            "about": f"Ingår i S&P 500 och handlas i New York. Sektor: {member['sector']}.",
        })  # fmt: skip

    funds, seen = [], set()
    rows = [r for r in screen(("fund", "STO")) if r.get("netAssets") and r.get("longName")]
    for row in sorted(rows, key=lambda r: (-r["netAssets"], len(r["longName"]), r["longName"])):
        if (
            row.get("currency") in allowed
            and _active(row, today)
            and fund_base(row["longName"]) not in seen
        ):
            seen.add(fund_base(row["longName"]))  # största andelsklassen först
            funds.append(row)
    for row in funds[:FUNDS] + [r for r in screen(("etf", "STO")) if r.get("longName")]:
        if row.get("currency") not in allowed or not _active(row, today):
            continue
        assets = row.get("netAssets") or 0
        billions = (
            f" Förvaltar cirka {assets / 1e9:.0f} miljarder {row['currency']}."
            if assets >= 1e9
            else ""
        )
        kind = (
            "Fond."
            if row.get("quoteType") == "MUTUALFUND"
            else "Börshandlad fond på Stockholmsbörsen."
        )
        add({
            "id": slug(row["symbol"]), "symbol": row["symbol"],
            "ticker": row["symbol"].removesuffix(".ST"), "name": row["longName"], "type": "fond",
            "sector": fund_sector(row["longName"]), "about": kind + billions,
        })  # fmt: skip

    for extra in EXTRAS:
        add(dict(extra))
    return _merge(curated, previous, found, fundamentals, today)


def _merge(curated, previous, found, fundamentals, today):
    """Behåll id:n för kända symboler, låt försvunna ligga kvar i GRACE_DAYS dagar."""
    earlier = {i["symbol"]: i for i in previous.get("instruments", [])}
    instruments = []
    for symbol, instrument in found.items():
        if symbol in earlier:
            instrument["id"] = earlier[symbol]["id"]
        instruments.append(instrument)
    for symbol, instrument in earlier.items():
        if symbol in found or symbol in {i["symbol"] for i in curated}:
            continue
        since = instrument.get("missingSince", today.isoformat())
        if date.fromisoformat(since) >= today - timedelta(days=GRACE_DAYS):
            instruments.append({**instrument, "missingSince": since})
    used = {i["id"] for i in curated}
    # Kända symboler behåller sina id:n; nya får ett tillägg om id:t redan är taget.
    for instrument in sorted(instruments, key=lambda i: (i["symbol"] not in earlier, i["symbol"])):
        base, n = instrument["id"], 2
        while instrument["id"] in used:
            instrument["id"], n = f"{base}-{n}", n + 1
        used.add(instrument["id"])
    keep = {i["symbol"] for i in instruments} | {i["symbol"] for i in curated}
    return {
        "generatedAt": today.isoformat(),
        "instruments": sorted(instruments, key=lambda i: i["id"]),
        "fundamentals": {s: f for s, f in sorted(fundamentals.items()) if f and s in keep},
    }


def yahoo_screen(spec):
    """Alla rader för en screenerfråga, 250 åt gången (Yahoos gräns)."""
    import yfinance as yf
    from yfinance import EquityQuery, ETFQuery, FundQuery

    kind, *args = spec
    query = {
        "equity": lambda: EquityQuery("eq", ["exchange", args[0]]),
        "industry": lambda: EquityQuery(
            "and",
            [EquityQuery("eq", ["exchange", args[0]]), EquityQuery("eq", ["industry", args[1]])],
        ),
        "us": lambda: EquityQuery(
            "and",
            [EquityQuery("eq", ["region", "us"]), EquityQuery("gt", ["intradaymarketcap", 2e9])],
        ),
        "fund": lambda: FundQuery("eq", ["exchange", args[0]]),
        "etf": lambda: ETFQuery("eq", ["exchange", args[0]]),
    }[kind]()
    rows, offset = [], 0
    while True:
        try:
            page = yf.screen(query, offset=offset, size=250, sortField="ticker", sortAsc=True)
        except Exception as exc:  # en misslyckad fråga ska inte stoppa resten
            print(f"fel  screener {spec}: {exc}", file=sys.stderr)
            return rows
        quotes = page.get("quotes", [])
        rows += quotes
        offset += len(quotes)
        if not quotes or offset >= page.get("total", 0):
            return rows
        time.sleep(0.3)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-age-days", type=int, help="bygg bara om listan är äldre än så")
    args = parser.parse_args()
    path, today = APP / "universe.json", date.today()
    try:
        previous = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        previous = {}
    if args.max_age_days is not None and previous.get("generatedAt"):
        if date.fromisoformat(previous["generatedAt"]) > today - timedelta(days=args.max_age_days):
            print(f"Listan från {previous['generatedAt']} är aktuell, bygger inte om.")
            return

    from yfinance.const import SECTOR_INDUSTY_MAPPING

    from finance.data import sp500_constituents

    config = json.loads((APP / "instruments.json").read_text("utf-8"))
    universe = build(
        config, previous, yahoo_screen, sp500_constituents, SECTOR_INDUSTY_MAPPING, today
    )
    if len(universe["instruments"]) < len(previous.get("instruments", [])) // 2:
        raise SystemExit(
            "Mindre än hälften så många instrument som förra gången; listan lämnas orörd."
        )
    path.write_text(json.dumps(universe, ensure_ascii=False, separators=(",", ":")), "utf-8")
    kinds = {}
    for instrument in universe["instruments"]:
        kinds[instrument["type"]] = kinds.get(instrument["type"], 0) + 1
    missing = sum("missingSince" in i for i in universe["instruments"])
    print(
        f"Klart: {len(universe['instruments'])} instrument {kinds}, {missing} saknas hos Yahoo just nu."
    )


if __name__ == "__main__":
    main()
