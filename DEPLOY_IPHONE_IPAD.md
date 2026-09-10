# Deploy V3 multi-source su iPhone/iPad

La strada più semplice è Streamlit Community Cloud.

## 1. GitHub
Il progetto è già nel repository `fanta-xi-assistant`.

## 2. Non caricare la chiave
La chiave API non deve stare nel codice. Il file `.gitignore` esclude `.env` e `.streamlit/secrets.toml`.

## 3. Streamlit Cloud
Vai su `share.streamlit.io`, accedi con GitHub, premi `Create app`, seleziona:
- repository: `gabb10/fanta-xi-assistant`;
- branch: `main` dopo il merge della PR;
- file: `app.py`.

## 4. Secrets
Nelle impostazioni avanzate incolla:

```toml
API_FOOTBALL_KEY = "LA_TUA_CHIAVE"
```

## 5. iPhone / iPad
Apri l'URL dell'app in Safari, poi usa **Condividi → Aggiungi a Home**.
