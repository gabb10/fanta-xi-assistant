from __future__ import annotations

import runpy
from pathlib import Path

import streamlit as st

APP = Path(__file__).resolve().with_name("app_v33.py")

try:
    runpy.run_path(str(APP), run_name="__main__")
except Exception as exc:
    st.error("L'app non è riuscita ad avviarsi correttamente.")
    st.code(f"{type(exc).__name__}: {exc}")
    st.caption("Apri Manage app per i log completi. La chiave API non viene mostrata qui.")
