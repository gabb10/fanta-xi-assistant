from __future__ import annotations

import runpy
import unicodedata
from pathlib import Path

import streamlit as st

# Compatibility layer V3.3.4
# - API-Football Free nel 2026 non espone le statistiche season=2026.
# - Gli ID giocatore vengono quindi risolti tramite /players/profiles (senza stagione).
# - Il calendario Serie A usa Fantacalcio.it come fonte primaria.
# - Avversario/casa-trasferta vengono ricavati anche dal nome squadra.
# - Il modulo mostrato e' SEMPRE 3-4-3; se un altro modulo e' sensibilmente piu sicuro,
#   l'app lo consiglia separatamente spiegando chi entra, chi esce e perche'.
import api_football
import competition_context
import fantasy_engine
from schedule_fallback import fantacalcio_schedule


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


# ---------------------------------------------------------------------------
# API-Football Free: risoluzione ID tramite profilo, senza season=2026
# ---------------------------------------------------------------------------
def _profile_row(player: dict) -> dict:
    return {"player": player or {}, "statistics": []}


def _search_player_profile_free(self, query: str, season: int | None = None, team_hint: str = ""):
    token = (query or "").split()[-1].replace("'", "").strip()
    if len(token) < 3:
        return None, 0

    result = self._get(
        "/players/profiles",
        {"search": token},
        ttl=30 * 24 * 3600,
    )
    if not result.response:
        return None, 0

    scored = []
    for item in result.response:
        player = item.get("player") or item
        score = self._name_score(query, player)
        scored.append((score, player))
    scored.sort(key=lambda x: x[0], reverse=True)

    score, player = scored[0]
    confidence = int(min(100, round(score)))
    row = _profile_row(player)
    self.remember_player_id(query, row, confidence)
    return row, confidence


def _player_profile_free(self, player_id: int, season: int | None = None):
    result = self._get(
        "/players/profiles",
        {"player": int(player_id)},
        ttl=30 * 24 * 3600,
    )
    if not result.response:
        return None
    item = result.response[0]
    player = item.get("player") or item
    return _profile_row(player)


api_football.APIFootball.search_player_stats = _search_player_profile_free
api_football.APIFootball.player_stats = _player_profile_free


# ---------------------------------------------------------------------------
# Calendario: sul piano Free evitiamo /fixtures?league=135&season=2026,
# che viene bloccato dalla limitazione stagionale. Fantacalcio.it contiene gia'
# le 10 partite della giornata con giorno e orario.
# ---------------------------------------------------------------------------
def _serie_a_schedule_free(api, season: int, count: int = 30):
    rows = fantacalcio_schedule(season)
    return rows[:count] if rows else []


competition_context.next_serie_a_fixtures = _serie_a_schedule_free

_original_team_id_from_fixture = competition_context.team_id_from_fixture


def _team_id_preserve_api(fixture, team_name: str = "", fallback_id=None):
    result = _original_team_id_from_fixture(fixture, team_name, fallback_id)
    return fallback_id if result is None and fallback_id is not None else result


competition_context.team_id_from_fixture = _team_id_preserve_api


# ---------------------------------------------------------------------------
# Avversario/data anche per fixture editoriali prive di ID API.
# ---------------------------------------------------------------------------
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
    stat_team = (((kwargs.get("stat_block") or {}).get("team") or {}).get("name", ""))
    target = _norm_team(stat_team or ev.team)

    if target and target == _norm_team(home_name):
        ev.team = stat_team or home_name
        ev.opponent = away_name or "—"
        ev.home_away = "Casa"
    elif target and target == _norm_team(away_name):
        ev.team = stat_team or away_name
        ev.opponent = home_name or "—"
        ev.home_away = "Trasferta"

    raw_date = (fixture.get("fixture") or {}).get("date")
    if raw_date:
        ev.kickoff = raw_date
    return ev


fantasy_engine.evaluate_player = _evaluate_player_schedule_safe


# ---------------------------------------------------------------------------
# Modulo: XI SEMPRE in 3-4-3.
# In parallelo calcoliamo il miglior modulo alternativo con il vecchio motore.
# Se l'alternativa aumenta davvero la sicurezza voto, mostriamo un consiglio,
# ma NON cambiamo automaticamente l'XI principale.
# ---------------------------------------------------------------------------
_original_best_lineup = fantasy_engine.best_lineup


def _pick_formation(players, formation: str):
    needs = fantasy_engine.FORMATIONS[formation]
    selector = getattr(fantasy_engine, "_selection_value", lambda p: p.schierabilita)
    chosen = []
    for role, n in needs.items():
        pool = sorted(
            [p for p in players if p.role == role],
            key=lambda p: (selector(p), p.p_vote, p.schierabilita, p.p_starter),
            reverse=True,
        )
        if len(pool) < n:
            return None
        chosen.extend(pool[:n])

    raw_total = sum(p.schierabilita for p in chosen)
    selection_total = sum(selector(p) for p in chosen)
    vote_floor = min(p.p_vote for p in chosen)
    expected_votes = sum(p.p_vote for p in chosen) / 100.0
    return {
        "formation": formation,
        "players": chosen,
        "total": raw_total,
        "optimizer_score": selection_total,
        "vote_floor": vote_floor,
        "expected_votes": expected_votes,
    }


def _player_diff(base_players, alt_players):
    base = {p.name: p for p in base_players}
    alt = {p.name: p for p in alt_players}
    out_players = [p for name, p in base.items() if name not in alt]
    in_players = [p for name, p in alt.items() if name not in base]
    return out_players, in_players


def _fmt_players(players):
    if not players:
        return "—"
    return ", ".join(f"{p.name.title()} ({p.role}, voto {p.p_vote:.0f}%)" for p in players)


def _best_lineup_343_always(players):
    base = _pick_formation(players, "3-4-3")
    if base is None:
        # Caso teorico: rosa incompleta. Solo qui lasciamo decidere al motore standard.
        return _original_best_lineup(players)

    alt = _original_best_lineup(players)
    if alt and alt.get("formation") != "3-4-3":
        alt_players = alt.get("players", [])
        alt_expected = sum(p.p_vote for p in alt_players) / 100.0 if alt_players else 0.0
        alt_floor = min((p.p_vote for p in alt_players), default=0.0)
        base_expected = base["expected_votes"]
        base_floor = base["vote_floor"]
        gain_expected = alt_expected - base_expected
        gain_floor = alt_floor - base_floor
        out_players, in_players = _player_diff(base["players"], alt_players)

        # Suggeriamo un cambio solo se c'e' un vantaggio di sicurezza percepibile:
        # almeno +0.25 voti attesi nell'XI, oppure +12 punti sul giocatore piu' a rischio.
        if gain_expected >= 0.25 or gain_floor >= 12:
            reasons = []
            if gain_expected >= 0.25:
                reasons.append(f"circa {gain_expected:.2f} voti attesi in piu' sull'XI")
            if gain_floor >= 12:
                reasons.append(
                    f"il giocatore piu' a rischio passa da {base_floor:.0f}% a {alt_floor:.0f}% di probabilita' voto"
                )
            st.warning(
                f"💡 **Consiglio modulo: valuta {alt['formation']}**\n\n"
                f"**Esce:** {_fmt_players(out_players)}  \n"
                f"**Entra:** {_fmt_players(in_players)}  \n"
                f"**Perche':** " + "; ".join(reasons) + ".  \n\n"
                f"La formazione principale resta comunque **3-4-3**, come richiesto."
            )

    return base


fantasy_engine.best_lineup = _best_lineup_343_always

APP = Path(__file__).resolve().with_name("app_v33.py")

try:
    runpy.run_path(str(APP), run_name="__main__")
except Exception as exc:
    st.error("L'app non e' riuscita ad avviarsi correttamente.")
    st.code(f"{type(exc).__name__}: {exc}")
    st.caption("Apri Manage app per i log completi. La chiave API non viene mostrata qui.")
