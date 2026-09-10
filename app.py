from __future__ import annotations

import runpy
from pathlib import Path

import streamlit as st

# Compatibility layer V3.3.1:
# 1) se API-Football non restituisce il calendario Serie A, usa Fantacalcio.it;
# 2) preferisce il 3-4-3 quando gli 11 scelti hanno tutti una buona probabilità di voto.
import competition_context
import fantasy_engine
from schedule_fallback import next_serie_a_fixtures_with_fallback

competition_context.next_serie_a_fixtures = next_serie_a_fixtures_with_fallback

_original_best_lineup = fantasy_engine.best_lineup
PREFERRED_343_VOTE_FLOOR = 70.0


def _safe_343(players):
    needs = fantasy_engine.FORMATIONS["3-4-3"]
    chosen = []
    selector = getattr(fantasy_engine, "_selection_value", lambda p: p.schierabilita)

    for role, n in needs.items():
        pool = sorted(
            [p for p in players if p.role == role],
            key=lambda p: (selector(p), p.p_vote, p.schierabilita, p.p_starter),
            reverse=True,
        )
        if len(pool) < n:
            return None
        chosen.extend(pool[:n])

    # "11 voti utili" viene interpretato in modo prudente: ogni titolare del 3-4-3
    # deve avere almeno il 70% di probabilità di prendere voto e non essere indisponibile.
    if any(p.p_vote < PREFERRED_343_VOTE_FLOOR or bool(p.unavailable) for p in chosen):
        return None

    raw_total = sum(p.schierabilita for p in chosen)
    selection_total = sum(selector(p) for p in chosen)
    vote_floor = min(p.p_vote for p in chosen)
    return {
        "formation": "3-4-3",
        "players": chosen,
        "total": raw_total,
        "optimizer_score": selection_total + 1000.0,
        "vote_floor": vote_floor,
        "preferred": True,
    }


def _best_lineup_343_first(players):
    preferred = _safe_343(players)
    if preferred is not None:
        return preferred
    return _original_best_lineup(players)


fantasy_engine.best_lineup = _best_lineup_343_first

APP = Path(__file__).resolve().with_name("app_v33.py")

try:
    runpy.run_path(str(APP), run_name="__main__")
except Exception as exc:
    st.error("L'app non è riuscita ad avviarsi correttamente.")
    st.code(f"{type(exc).__name__}: {exc}")
    st.caption("Apri Manage app per i log completi. La chiave API non viene mostrata qui.")
