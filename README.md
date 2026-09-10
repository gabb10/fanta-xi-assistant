# Fanta XI Assistant V3.1 — Multi-source

Web app Streamlit ottimizzata per iPhone/iPad e desktop, pensata per una lega Fantacalcio Classic a 10 senza modificatore difesa.

## Cosa fa

- incrocia API-Football, Fantacalcio.it, Gazzetta e Sky Sport;
- legge notizie recenti da più testate tramite Google News RSS;
- distingue formazione ufficiale, probabili, ballottaggi, panchina e indisponibilità;
- calcola `% titolare`, `% schierabilità`, consenso tra fonti e confidenza;
- considera forma, matchup, rigori/piazzati e, opzionalmente, ultime 5 avanzate;
- confronta i moduli Classic e propone XI + panchina;
- segnala quando le fonti sono molto discordanti.

## Novità V3.1

La V3.1 è ottimizzata per il piano Free di API-Football:

- risolve giocatore + statistiche stagionali con una sola richiesta quando possibile;
- recupera le prossime partite per campionato, invece di fare una richiesta per ogni squadra;
- legge i rate-limit restituiti da API-Football e rallenta automaticamente prima di superarli;
- conserva le risposte in cache per ridurre le richieste dei successivi aggiornamenti;
- mostra chiamate di rete, cache hit, quota giornaliera, quota al minuto e secondi di attesa;
- sostituisce il generico `dati API parziali` con diagnostica più utile nella scheda Fonti;
- corregge il contatore dei segnali includendo anche Fantacalcio.it;
- lascia `Matchup API` e `Ultime 5 avanzate` disattivati di default per non consumare inutilmente il piano Free.

## Fonti

- API-Football: fixture, statistiche, infortuni, lineup ufficiali e prediction opzionali.
- Fantacalcio.it: percentuali probabili e gerarchie rigoristi/piazzati.
- La Gazzetta dello Sport: probabili formazioni e ballottaggi.
- Sky Sport: probabili, riserve, dubbi, squalificati e indisponibili.
- News recenti: Sky, Gazzetta, Fantacalcio.it, Corriere dello Sport e Tuttomercatoweb.

Le fonti editoriali sono best-effort: se una testata modifica l'HTML o limita l'accesso automatico, il programma continua a funzionare usando le altre fonti disponibili.

## Regola fondamentale

Una percentuale pubblicata realmente da una fonte viene distinta da un segnale numerico interno. Per esempio, se Sky indica un giocatore nell'XI probabile ma non pubblica una percentuale, l'app converte quello stato in un valore interno solo per il calcolo del consenso, senza presentarlo come una percentuale ufficiale di Sky.

La formazione ufficiale prevale sempre sugli altri segnali.

## Deploy su Streamlit Community Cloud

1. Repository: `gabb10/fanta-xi-assistant`.
2. Branch: `main`.
3. Main file path: `app.py`.
4. Nei Secrets inserisci:

```toml
API_FOOTBALL_KEY = "LA_TUA_CHIAVE"
```

Quando `main` viene aggiornato, Streamlit Community Cloud normalmente rileva il nuovo commit e ridistribuisce l'app collegata al repository.

Non inserire mai la chiave API nel repository pubblico.
