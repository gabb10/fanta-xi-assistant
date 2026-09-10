# Fanta XI Assistant

Web app/PWA per aiutare a scegliere la formazione migliore in un Fantacalcio a 10, senza modificatore difesa.

## Obiettivo

Il progetto è pensato per incrociare più segnali prima della giornata: probabili formazioni, indisponibili, squalifiche, stato di forma e notizie. L'algoritmo assegna un punteggio a ogni giocatore e confronta i moduli consentiti per proporre XI titolare e panchina.

## Stato attuale

Questa è la prima versione funzionante dello scaffold:

- React + Vite
- layout responsive per iPhone/iPad/desktop
- PWA installabile dalla Home
- ottimizzatore dei moduli senza modificatore difesa
- dati demo sostituibili con dati reali
- workflow GitHub Pages
- struttura pronta per aggiungere adapter multi-fonte

## Avvio locale

```bash
npm install
npm run dev
```

## Build

```bash
npm run build
```

## Pubblicazione

Il workflow `.github/workflows/deploy-pages.yml` compila il progetto e pubblica `dist/` su GitHub Pages. Nelle impostazioni del repository abilita **Settings > Pages > Source: GitHub Actions**.

## Dati reali

I siti editoriali e di probabili formazioni hanno formati e condizioni d'uso differenti. Gli adapter reali vanno aggiunti in modo specifico per ogni fonte, preferendo API/RSS ufficiali quando disponibili. Il file `public/data/latest.json` definisce il formato dati usato dall'interfaccia.

## Roadmap

1. Inserimento/importazione della propria rosa.
2. Adapter per più fonti e normalizzazione nomi giocatori.
3. Titolarità e indisponibilità aggiornate automaticamente.
4. News per giocatore con affidabilità della fonte.
5. Punteggio predittivo più evoluto basato su avversario, forma e minutaggio.
6. Notifiche pre-consegna formazione.
