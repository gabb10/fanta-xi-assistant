# Fanta XI Assistant V3 — Multi-source

Web app Streamlit ottimizzata per iPhone/iPad e desktop, pensata per una lega Fantacalcio Classic a 10 senza modificatore difesa.

## Cosa fa

- incrocia API-Football, Fantacalcio.it, Gazzetta e Sky Sport;
- legge notizie recenti da più testate tramite Google News RSS;
- distingue formazione ufficiale, probabili, ballottaggi, panchina e indisponibilità;
- calcola `% titolare`, `% schierabilità`, consenso tra fonti e confidenza;
- considera forma, matchup, ultime 5, rigori/piazzati e pericolosità offensiva;
- confronta i moduli Classic e propone XI + panchina;
- segnala quando le fonti sono molto discordanti.

## Fonti

- API-Football: fixture, statistiche, infortuni, lineup ufficiali, prediction.
- Fantacalcio.it: percentuali probabili e gerarchie rigoristi/piazzati.
- La Gazzetta dello Sport: probabili formazioni e ballottaggi.
- Sky Sport: probabili, riserve, dubbi, squalificati e indisponibili.
- News recenti: Sky, Gazzetta, Fantacalcio.it, Corriere dello Sport e Tuttomercatoweb.

Le fonti editoriali sono best-effort: se una testata modifica l'HTML o limita l'accesso automatico, il programma continua a funzionare usando le altre fonti disponibili.

## Regola fondamentale

Una percentuale pubblicata realmente da una fonte viene distinta da un segnale numerico interno. Per esempio, se Sky indica un giocatore nell'XI probabile ma non pubblica una percentuale, l'app converte quello stato in un valore interno solo per il calcolo del consenso, senza presentarlo come una percentuale ufficiale di Sky.

La formazione ufficiale prevale sempre sugli altri segnali.

## Deploy su Streamlit Community Cloud

1. Fai merge della PR `streamlit-v3` su `main`.
2. Apri Streamlit Community Cloud e crea una nuova app.
3. Repository: `gabb10/fanta-xi-assistant`.
4. Branch: `main`.
5. Main file path: `app.py`.
6. Nei Secrets inserisci:

```toml
API_FOOTBALL_KEY = "LA_TUA_CHIAVE"
```

7. Apri il link `.streamlit.app` da Safari su iPhone/iPad.
8. Usa **Condividi → Aggiungi a Home**.

Non inserire mai la chiave API nel repository pubblico.
