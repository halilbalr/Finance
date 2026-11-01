#!/usr/bin/env node
// Hämtar kurser från Yahoo Finance och skriver data/quotes.json och data/meta.json.
// Kör: node scripts/fetch-yahoo.mjs   (eller npm run update)
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { fetchChart, toQuote } from './yahoo.mjs';

const root = new URL('../', import.meta.url);
const dataDir = new URL('data/', root);
const cfg = JSON.parse(await readFile(new URL('instruments.json', root), 'utf8'));
const readJson = async (name, fallback) => { try { return JSON.parse(await readFile(new URL(name, dataDir), 'utf8')); } catch { return fallback; } };

const prev = await readJson('quotes.json', { quotes: [] });
const prevById = Object.fromEntries((prev.quotes || []).map(q => [q.id, q])); // även exempelkurser, så att ett innehav aldrig försvinner
const now = new Date().toISOString();
const quotes = [], errors = [];

for (const inst of cfg.instruments) {
  try {
    quotes.push(toQuote(inst, await fetchChart(inst.symbol, '1y'), now));
    console.log(`ok   ${inst.symbol}`);
  } catch (e) {
    errors.push({ id: inst.id, symbol: inst.symbol, error: String(e.message || e) });
    console.warn(`fel  ${inst.symbol}: ${e.message || e}`);
    if (prevById[inst.id]) quotes.push({ ...prevById[inst.id], stale: true }); // behåll senaste lyckade hämtningen
  }
}

const prevMeta = await readJson('meta.json', {});
const fx = { SEK: 1, ...(prevMeta.fxDemo ? {} : prevMeta.fx || {}) };
let fxOk = true;
for (const [cur, sym] of Object.entries(cfg.fx || {})) {
  try { const r = await fetchChart(sym, '5d'); fx[cur] = +(+r.meta.regularMarketPrice).toFixed(4); }
  catch (e) { fxOk = false; errors.push({ id: 'fx-' + cur, symbol: sym, error: String(e.message || e) }); }
}

const fresh = quotes.filter(q => !q.stale).length;
if (!fresh) {
  console.error('Ingen kurs kunde hämtas. Filerna lämnas orörda.');
  process.exit(1);
}

await mkdir(dataDir, { recursive: true });
await writeFile(new URL('quotes.json', dataDir), JSON.stringify({ updatedAt: now, source: 'Yahoo Finance', quotes, errors }));
await writeFile(new URL('meta.json', dataDir), JSON.stringify({
  ...prevMeta, pricesDemo: false, pricesUpdatedAt: now, source: 'Yahoo Finance', fx, fxDemo: !fxOk && !!prevMeta.fxDemo,
}, null, 2));
console.log(`Klart: ${fresh} av ${cfg.instruments.length} hämtade${errors.length ? `, ${errors.length} fel` : ''}.`);
