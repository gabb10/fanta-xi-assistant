from __future__ import annotations

import runpy
from pathlib import Path

import pandas as pd
import streamlit as st

# V3.4.1: carica tutte le correzioni/estensioni prima dell'interfaccia:
# API Free, calendario Serie A, 3-4-3 fisso, infortuni/squalifiche e bonus potential.
# In fondo aggiunge anche una tabella compatta per verificare gli ID API risolti,
# senza fare nuove chiamate di rete.
import compat_v34  # noqa: F401

APP = Path(__file__).resolve().with_name("app_v33.py")

try:
    runpy.run_path(str(APP), run_name="__main__")

    evaluations = st.session_state.get("evaluations_v33") or []
    status = st.session_state.get("v33_sources") or {}
    debug = status.get("debug") or {}

    if evaluations and debug:
        st.markdown("---")
        st.subheader("🪪 Verifica ID API")
        st.caption("Questa tabella usa i risultati già caricati: non consuma altre richieste API.")

        rows = []
        for p in evaluations:
            key = " ".join(str(p.name).upper().replace("-", " ").replace(".", " ").replace("'", " ").split())
            info = debug.get(key, {})
            pid = info.get("api_id")
            conf = int(info.get("api_confidence") or 0)
            resolved = info.get("api_resolved") or "—"
            err = info.get("api_error") or ""
            if pid and conf >= 90:
                state = "✅ OK"
            elif pid and conf >= 75:
                state = "🟡 Controllare"
            else:
                state = "🔴 Da verificare"
            rows.append({
                "Giocatore": p.name.title(),
                "API ID": pid if pid else "—",
                "Nome trovato": resolved,
                "Confidenza": f"{conf}%",
                "Stato": state,
                "Errore": err,
            })

        df_ids = pd.DataFrame(rows)
        rank = {"🔴 Da verificare": 0, "🟡 Controllare": 1, "✅ OK": 2}
        df_ids["_ordine"] = df_ids["Stato"].map(rank).fillna(9)
        df_ids = df_ids.sort_values(["_ordine", "Giocatore"]).drop(columns=["_ordine"])
        st.dataframe(df_ids, hide_index=True, use_container_width=True)

        needs_check = df_ids[df_ids["Stato"] != "✅ OK"]
        if len(needs_check):
            st.warning(f"Da verificare prima del salvataggio permanente: {len(needs_check)} giocatore/i.")
        else:
            st.success("Tutti gli ID risultano ad alta confidenza. Possiamo fissarli nel roster dopo un controllo finale.")

except Exception as exc:
    st.error("L'app non e' riuscita ad avviarsi correttamente.")
    st.code(f"{type(exc).__name__}: {exc}")
    st.caption("Apri Manage app per i log completi. La chiave API non viene mostrata qui.")
