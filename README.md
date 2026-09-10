# Fanta XI Assistant V3.1 — Multi-source

Web app Streamlit ottimizzata per iPhone/iPad e desktop, pensata per una lega Fantacalcio Classic a 10 senza modificatore difesa.

## Fonti

- API-Football: statistiche, fixture, infortuni, lineup ufficiali e prediction opzionali.
- Fantacalcio.it: percentuali probabili, team-hint e gerarchie rigoristi/piazzati.
- Gazzetta dello Sport: probabili e ballottaggi.
- Sky Sport: probabili, riserve, dubbi e indisponibili.
- News recenti: Sky, Gazzetta, Fantacalcio.it, Corriere dello Sport e Tuttomercatoweb.

## Novità V3.1

La V3.1 è ottimizzata per il piano gratuito API-Football:

- rispetta automaticamente il limite per minuto usando gli header dell'API;
- applica retry con backoff in caso di HTTP 429;
- cerca profilo e statistiche con una sola richiesta quando l'ID non è noto;
- memorizza gli ID giocatore risolti per evitare abbinamenti ripetuti;
- usa il team rilevato da Fantacalcio.it per disambiguare omonimi;
- raggruppa le prossime fixture per campionato per ridurre le chiamate;
- mantiene Matchup/Predictions disattivato di default sul piano Free;
- mostra diagnostica di quota giornaliera/minuto, cache hit, attese ed errori;
- il contatore `Fonti probabili` include ora anche Fantacalcio.it.

## Metodo

La formazione ufficiale prevale sempre. Le probabili editoriali vengono incrociate con statistiche e news. Quando Sky o Gazzetta indicano semplicemente XI/panchina/dubbio senza pubblicare una percentuale, l'app usa un valore interno solo per il calcolo del consenso e non lo presenta come percentuale ufficiale della testata.

## Streamlit Community Cloud

Repository: `gabb10/fanta-xi-assistant`  
Branch: `main`  
Entry point: `app.py`

Nei Secrets di Streamlit:

```toml
API_FOOTBALL_KEY = "LA_TUA_CHIAVE"
```

Non inserire mai la chiave API nel repository pubblico.
