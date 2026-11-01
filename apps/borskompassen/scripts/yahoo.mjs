// Hjälpfunktioner för Yahoo Finance chart-API:t (v8). Inga beroenden, kräver Node 18+.
const HOSTS = ['https://query1.finance.yahoo.com', 'https://query2.finance.yahoo.com'];
const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36';
const sleep = ms => new Promise(r => setTimeout(r, ms));

export async function fetchChart(symbol, range = '1y', fetchImpl = fetch) {
  let lastErr;
  for (const host of HOSTS) {
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const url = `${host}/v8/finance/chart/${encodeURIComponent(symbol)}?range=${range}&interval=1d&includePrePost=false&events=div`;
        const res = await fetchImpl(url, { headers: { 'User-Agent': UA, Accept: 'application/json' } });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = await res.json();
        const result = json?.chart?.result?.[0];
        if (!result) throw new Error(json?.chart?.error?.description || 'tomt svar');
        return result;
      } catch (e) {
        lastErr = e;
        await sleep(700 * (attempt + 1));
      }
    }
  }
  throw new Error(`${symbol}: ${lastErr?.message || lastErr}`);
}

const isoDay = (unixSec, offsetSec = 0) => new Date((unixSec + offsetSec) * 1000).toISOString().slice(0, 10);
const round = v => (v == null || !isFinite(v)) ? null : +v.toFixed(Math.abs(v) < 10 ? 4 : Math.abs(v) < 1000 ? 2 : 1);

// Gör om ett chart-svar till det format appen läser (ett "quote"-objekt).
export function toQuote(inst, result, updatedAt = new Date().toISOString()) {
  const m = result.meta || {}, off = m.gmtoffset || 0;
  const ts = result.timestamp || [], cl = result.indicators?.quote?.[0]?.close || [];
  const byDay = new Map();
  ts.forEach((t, k) => { const c = cl[k]; if (c != null && isFinite(c)) byDay.set(isoDay(t, off), c); });
  const dates = [...byDay.keys()].sort();
  const close = dates.map(d => byDay.get(d));
  const price = m.regularMarketPrice ?? close[close.length - 1];
  const pdate = m.regularMarketTime ? isoDay(m.regularMarketTime, off) : dates[dates.length - 1];
  if (price != null && pdate) {
    if (dates[dates.length - 1] === pdate) close[close.length - 1] = price;
    else if (!dates.length || pdate > dates[dates.length - 1]) { dates.push(pdate); close.push(price); }
  }
  if (!close.length) throw new Error(`${inst.symbol}: inga kurser i svaret`);
  const yr = close.slice(-252);
  // Utdelningar senaste året ger direktavkastning
  const divs = Object.values(result.events?.dividends || {}).filter(d => d.date >= (Date.now() / 1000 - 365 * 86400));
  const divSum = divs.reduce((s, d) => s + (d.amount || 0), 0);
  return {
    id: inst.id, symbol: inst.symbol, ticker: inst.ticker || inst.symbol, name: inst.name || m.longName || m.shortName || inst.symbol,
    type: inst.type || 'aktie', sector: inst.sector || 'Annat', currency: m.currency || 'SEK',
    price: round(price), prevClose: round(close.length > 1 ? close[close.length - 2] : m.chartPreviousClose),
    date: dates[dates.length - 1],
    high52: round(m.fiftyTwoWeekHigh ?? Math.max(...yr)), low52: round(m.fiftyTwoWeekLow ?? Math.min(...yr)),
    pe: null, dividendYield: divSum > 0 && price ? +((divSum / price) * 100).toFixed(2) : (inst.type === 'fond' ? null : 0),
    about: inst.about || '',
    history: { start: dates[0], dates, close: close.map(round) },
    demo: false, updatedAt,
  };
}
