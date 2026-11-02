# Börskompassen

En app för mobil och dator där du håller koll på dina aktier och fonder. Den visar köp- och säljsignaler med förklaringar, larmar när en kurs passerar en gräns och förklarar hur händelser i världen kan påverka dina innehav. Kurserna hämtas från Yahoo Finance.

Appen är en enda webbsida (`index.html`) utan byggsteg. Kurserna ligger i JSON-filer under `data/`, och ett litet skript hämtar nya kurser från Yahoo Finance och skriver dem dit.

Appen följer omkring 2 200 instrument: alla aktier med börsvärde på Stockholms-, Oslo-, Helsingfors- och Köpenhamnsbörsen, S&P 500, de 300 största svenska fonderna, börshandlade fonder i Stockholm, de stora börsindexen, råvaror som olja och guld, kryptovalutor och valutor.

## Kom igång på din egen dator

Du behöver [Python](https://www.python.org) 3.12 eller senare och [Node.js](https://nodejs.org) version 18 eller senare. Kurserna hämtas med repots Python-paket `finance`, som du installerar en gång från repots rot:

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e '.[data,dev]'
```

Gå sedan till `apps/borskompassen` (med samma miljö aktiverad):

```bash
npm run universe # bygger listan över alla aktier och fonder (universe.json), tar några minuter
npm run update   # hämtar kurser från Yahoo Finance till data/, cirka 10 minuter med hela listan
npm start        # startar appen på http://localhost:8080
npm test         # testar hämtskripten
```

Utan `npm run universe` hämtar `npm run update` bara instrumenten i `instruments.json`. Öppna appen via `npm start`. Om du dubbelklickar på `index.html` kan webbläsaren inte läsa datafilerna.

Innan du har kört `npm run update` visar appen exempelkurser, och det står tydligt i appen.

## Lägg upp den på GitHub (gratis webbadress)

Appen ligger i `apps/borskompassen` i repot, och automatiken i repots `.github/workflows/update-data.yml`.

1. Gå till repots **Settings → Pages** och välj **Source: GitHub Actions**.
2. Gå till **Actions → Uppdatera kurser och publicera → Run workflow**.

Efter någon minut ligger appen på `https://<ditt-användarnamn>.github.io/<repo-namn>/`. Automatiken hämtar sedan nya kurser tre gånger varje vardag:

- på morgonen
- efter att Stockholmsbörsen har stängt
- efter att New York-börsen har stängt

Varje gång publiceras sidan på nytt med de nya kurserna. Den publiceras också när appen eller hämtkoden ändras på repots huvudgren (`master`). Tiderna står i `.github/workflows/update-data.yml`.

Kursfilen blir omkring 12 MB och sparas därför inte i repot vid varje körning, eftersom repot annars skulle växa med flera hundra MB om året. Varje körning utgår i stället från den senast publicerade versionen på sidan. Filerna i `data/` i repot är en äldre kopia som bara används om sidan inte går att nå. Listan över instrument (`universe.json`) byggs om en gång i veckan på samma sätt.

Lägg sidan på hemskärmen i mobilen så fungerar den som en app. I Safari väljer du Dela och sedan Lägg till på hemskärmen. I Chrome väljer du menyn och sedan Lägg till på startskärmen.

## Lägg till aktier och fonder

De flesta instrument kommer automatiskt från Yahoos listor via `build_universe.py`, som skriver `universe.json`. Där sätts branschen utifrån Yahoos branschindelning (och för fonder utifrån namnet), och beskrivningen blir en kort standardtext. Ett instrument som försvinner ur Yahoos listor ligger kvar i 30 dagar innan det tas bort.

Vill du ha en egen beskrivning, en annan bransch eller ett instrument som inte kommer med automatiskt lägger du det i `instruments.json`. Det går alltid före den automatiska listan. Lägg till en rad med Yahoos kortnamn i `symbol`:

| Marknad | Exempel på `symbol` |
|---|---|
| Stockholmsbörsen | `VOLV-B.ST`, `INVE-B.ST`, `SAAB-B.ST` |
| Oslobörsen | `EQNR.OL` |
| USA | `AAPL`, `NVDA` |
| Index | `^OMX` (OMXS30) |
| Svenska fonder | Sök fondens namn på [finance.yahoo.com](https://finance.yahoo.com). Kortnamnet börjar ofta med `0P` |

`sector` ska vara en av branscherna appen känner till: Bank, Industri, Försvar, Teknik, Handel, Läkemedel, Gruvor & metaller, Energi, Spel, Investmentbolag, Fastigheter, Globalfond, Sverigefond, Räntor eller Annat. Branschen avgör hur omvärldsanalysen och "Vad händer om …?" kopplas till aktien.

`id` får inte ändras för en aktie som redan finns i någons portfölj, eftersom affärerna pekar på det.

Har du en aktie eller fond som inte finns med kan du också lägga till den direkt i appen under **Marknad → Lägg till egen** och skriva in kursen själv.

## Omvärldsanalysen

Analyserna ligger i `data/briefs.json`. Varje analys har ett datum, en sammanfattning och ett antal händelser. Varje händelse har:

- en kedja från händelse till aktiekurs (`chain`)
- vilka branscher som påverkas, åt vilket håll och varför (`impacts`)
- länkar till källorna (`sources`)

Kopiera den befintliga analysen som mall när du lägger in en ny. Appen visar den nyaste och låter dig bläddra bland de äldre.

Hämtskriptet rör inte den här filen. Den uppdateras bara när du själv, eller någon du ber om hjälp, lägger in en ny analys.

## Var sparas mina affärer?

I webbläsaren, på den enhet du använder. Inget skickas till någon server. Vill du se samma portfölj i mobilen och på datorn går du till **Hjälp → Inställningar**. Där sparar du en säkerhetskopia och läser in den på den andra enheten.

## Bra att veta

- Yahoo Finance har inget officiellt API. Skriptet hämtar via repots `finance.data.YahooFinance`, som bygger på paketet yfinance. Gränssnitten kan ändras eller strypas utan förvarning. Om en aktie inte kan hämtas behåller skriptet den senaste lyckade kursen och skriver felet i `data/quotes.json`.
- Direktavkastning och P/E-tal är Yahoos siffror för aktier, räknade om mot dagens kurs. P/E visas i läget Avancerat. Saknar Yahoo en siffra visas den inte. Fonder, index och råvaror har inga sådana siffror.
- Med så många instrument är kursfilen omkring 2 MB över nätet varje gång appen öppnas. Marknadslistan visar 100 rader åt gången; sökningen går igenom alla.
- Aktier i Köpenhamn handlas i danska kronor och index som FTSE 100, Nikkei och Hang Seng i pund, yen och Hongkongdollar. De räknas om till kronor med växelkurserna i `data/meta.json`.
- Kurserna är fördröjda och uppdateras bara när skriptet körs. Appen är inte gjord för daytrading.
- Yahoos villkor tillåter personligt bruk. Kontrollera villkoren innan du använder datan i en tjänst för andra.
- Signalerna är regler som räknas fram från kurshistoriken och omvärldsanalysen. De är inte finansiell rådgivning och slår ofta fel.

## Filer

| Fil | Innehåll |
|---|---|
| `index.html` | Hela appen: utseende, beräkningar, signaler, larm och hjälp |
| `instruments.json` | Handplockade aktier och fonder med egna beskrivningar, och valutorna som hämtas |
| `build_universe.py` | Bygger `universe.json`: alla övriga aktier, fonder och annat från Yahoos listor |
| `universe.json` | Skapas av `build_universe.py`, finns inte i repot utan på den publicerade sidan |
| `update_data.py` | Hämtar kurser via repots `finance`-paket och skriver `data/quotes.json` och `data/meta.json` |
| `data/` | Kurser, valutakurser, omvärldsanalyser och kalender |
| `server.mjs` | Enkel webbserver för att köra appen lokalt |
| `tests/test_borskompassen*.py` (i repots rot) | Tester för hämtningen och listan, med påhittade kurser |
| `.github/workflows/update-data.yml` (i repots rot) | Hämtar kurser enligt schema och publicerar på GitHub Pages |
