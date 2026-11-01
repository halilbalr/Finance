import { test } from 'node:test';
import assert from 'node:assert/strict';
import { toQuote, fetchChart } from '../scripts/yahoo.mjs';

// Ett förenklat svar i samma form som Yahoos chart-API (v8)
const day = d => Date.UTC(2026, 9, d, 8, 0) / 1000; // 8:00 UTC
const sample = {
  meta: { currency: 'SEK', symbol: 'VOLV-B.ST', regularMarketPrice: 290.4, regularMarketTime: day(30) + 3600 * 7, gmtoffset: 3600, fiftyTwoWeekHigh: 310, fiftyTwoWeekLow: 240, chartPreviousClose: 250 },
  timestamp: [day(27), day(28), day(29), day(29), day(30)],
  indicators: { quote: [{ close: [280, null, 285.5, 286, 289.9] }] },
  events: { dividends: { a: { amount: 18, date: Date.now() / 1000 - 86400 * 100 } } },
};
const inst = { id: 'volv-b', symbol: 'VOLV-B.ST', ticker: 'VOLV B', name: 'Volvo B', type: 'aktie', sector: 'Industri' };

test('gör om chart-svar till appens format', () => {
  const q = toQuote(inst, sample, 'T');
  assert.deepEqual(q.history.dates, ['2026-10-27', '2026-10-29', '2026-10-30']); // null bort, dubbletter sammanslagna
  assert.equal(q.price, 290.4);                    // senaste kurs ersätter dagens stängning
  assert.equal(q.history.close.at(-1), 290.4);
  assert.equal(q.prevClose, 286);                  // föregående dags (sista) värde
  assert.equal(q.date, '2026-10-30');
  assert.equal(q.high52, 310);
  assert.equal(q.dividendYield, +(18 / 290.4 * 100).toFixed(2));
  assert.equal(q.demo, false);
});

test('lägger till dagens kurs om den saknas i serien', () => {
  const s = structuredClone(sample); s.meta.regularMarketTime = day(31);
  const q = toQuote(inst, s);
  assert.equal(q.history.dates.at(-1), '2026-10-31');
  assert.equal(q.prevClose, 289.9);
});

test('provar reservservern vid fel', async () => {
  let calls = 0;
  const fake = async url => { calls++; if (url.startsWith('https://query1')) return { ok: false, status: 429 }; return { ok: true, json: async () => ({ chart: { result: [sample] } }) }; };
  const r = await fetchChart('VOLV-B.ST', '1y', fake);
  assert.equal(r.meta.symbol, 'VOLV-B.ST');
  assert.equal(calls, 3);
});
