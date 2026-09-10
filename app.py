from __future__ import annotations

import runpy
import unicodedata
from pathlib import Path

import streamlit as st

# Compatibility layer V3.3.2:
# 1) calendario Serie A: API-Football + fallback Fantacalcio.it;
# 2) conserva l'ID squadra API anche se la fixture editoriale non ha ID;
# 3) avversario/casa-trasferta ricavati anche dal nome squadra;
# 4) 3-4-3 preferito quando tutti gli 11 hanno una buona probabilità di voto.
import competition_context
import fantasy_engine
from schedule_fallback import next_serie_a_fixtures_with_fallback


def _norm_team(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.upper().replace("-", " ").replace(".", " ").replace("'", " ")
    value = " ".join(value.split())
    aliases = {
        "AC MILAN": "MILAN",
        "FC INTERNAZIONALE MILANO": "INTER",
        "INTERNAZIONALE": "INTER",
        "HELLAS VERONA": "VERONA",
    }
    return aliases.get(value, value)


# --- Calendario fallback ---
competition_context.next_serie_a_fixtures = next_serie_a_fixtures_with_fallback

_original_team_id_from_fixture = competition_context.team_id_from_fixture


def _team_id_preserve_api(fixture, team_name: str = "", fallback_id=None):
    result = _original_team_id_from_fixture(fixture, team_name, fallback_id)
    # Le fixture editoriali hanno volutamente id=None. In quel caso non perdiamo
    # l'ID reale già ottenuto dalle statistiche API: serve per coppe e turnover.
    return fallback_id if result is None and fallback_id is not None else result


competition_context.team_id_from_fixture = _team_id_preserve_api


# --- Avversario/data anche per fixture editoriali senza ID ---
_original_evaluate_player = fantasy_engine.evaluate_player


def _evaluate_player_schedule_safe(*args, **kwargs):
    ev = _original_evaluate_player(*args, **kwargs)
    fixture = kwargs.get("fixture")
    if not fixture:
        return ev

    teams = fixture.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    home_name = home.get("name", "")
    away_name = away.get("name", "")
    target = _norm_team(ev.team or (((kwargs.get("stat_block") or {}).get("team") or {}).get("name", "")))

    if target and target == _norm_team(home_name):
        ev.opponent = away_name or "—"
        ev.home_away = "Casa"
    elif target and target == _norm_team(away_name):
        ev.opponent = home_name or "—"
        ev.home_away = "Trasferta"

    raw_date = (fixture.get("fixture") or {}).get("date")
    if raw_date:
        ev.kickoff = raw_date
    return ev


fantasy_engine.evaluate_player = _evaluate_player_schedule_safe


# --- Preferenza 3-4-3 ---
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

    # Interpretazione prudente di "11 voti utili": ogni giocatore scelto nel
    # 3-4-3 deve avere almeno il 70% di probabilità di voto e non essere out.
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
