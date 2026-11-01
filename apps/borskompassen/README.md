# Börskompassen

En app för mobil och dator där du håller koll på dina aktier och fonder. Den visar köp- och säljsignaler med förklaringar, larmar när en kurs passerar en gräns och förklarar hur händelser i världen kan påverka dina innehav. Kurserna hämtas från Yahoo Finance.

Appen är en enda webbsida (`index.html`) utan byggsteg. Kurserna ligger i JSON-filer under `data/`, och ett litet skript hämtar nya kurser från Yahoo Finance och skriver dem dit.

## Kom igång på din egen dator

Du behöver [Node.js](https://nodejs.org) version 18 eller senare. Inga paket behöver installeras.

```bash
npm run update   # hämtar kurser från Yahoo Finance till data/
npm start        # startar appen på http://localhost:8080
npm test         # testar hämtskriptet
```

Öppna appen via `npm start`. Om du dubbelklickar på `index.html` kan webbläsaren inte läsa datafilerna.

Innan du har kört `npm run update` visar appen exempelkurser, och det står tydligt i appen.

## Lägg upp den på GitHub (gratis webbadress)

1. Skapa ett nytt repo på GitHub och ladda upp innehållet i den här mappen, inklusive den dolda mappen `.github`.
2. Gå till **Settings → Pages** och välj **Source: GitHub Actions**.
3. Gå till **Actions → Uppdatera kurser och publicera → Run workflow**.

Efter någon minut ligger appen på `https://<ditt-användarnamn>.github.io/<repo-namn>/`. Automatiken hämtar sedan nya kurser tre gånger varje vardag:

- på morgonen
- efter att Stockholmsbörsen har stängt
- efter att New York-börsen har stängt

Varje gång sparas kurserna i `data/` och sidan publiceras på nytt. Tiderna står i `.github/workflows/update-data.yml`.

Lägg sidan på hemskärmen i mobilen så fungerar den som en app. I Safari väljer du Dela och sedan Lägg till på hemskärmen. I Chrome väljer du menyn och sedan Lägg till på startskärmen.

## Lägg till aktier och fonder

Alla aktier och fonder som hämtas står i `instruments.json`. Lägg till en rad med Yahoos kortnamn i `symbol`:

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

- Yahoo Finance har inget officiellt API. Chart-gränssnittet som skriptet använder (`/v8/finance/chart`) är öppet men kan ändras eller strypas utan förvarning. Om en aktie inte kan hämtas behåller skriptet den senaste lyckade kursen och skriver felet i `data/quotes.json`.
- Kurserna är fördröjda och uppdateras bara när skriptet körs. Appen är inte gjord för daytrading.
- Yahoos villkor tillåter personligt bruk. Kontrollera villkoren innan du använder datan i en tjänst för andra.
- Signalerna är regler som räknas fram från kurshistoriken och omvärldsanalysen. De är inte finansiell rådgivning och slår ofta fel.

## Filer

| Fil | Innehåll |
|---|---|
| `index.html` | Hela appen: utseende, beräkningar, signaler, larm och hjälp |
| `instruments.json` | Aktier, fonder och valutor som hämtas |
| `scripts/fetch-yahoo.mjs` | Hämtar kurser och skriver `data/quotes.json` och `data/meta.json` |
| `scripts/yahoo.mjs` | Anropet till Yahoo och omvandlingen till appens format |
| `data/` | Kurser, valutakurser, omvärldsanalyser och kalender |
| `server.mjs` | Enkel webbserver för att köra appen lokalt |
| `test/` | Tester för hämtningen, med ett påhittat Yahoo-svar |
| `.github/workflows/update-data.yml` | Hämtar kurser enligt schema och publicerar på GitHub Pages |
