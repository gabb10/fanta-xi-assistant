from __future__ import annotations

import csv
import unicodedata
from pathlib import Path

import streamlit as st

import api_football
import competition_context
import fantasy_engine
from fantasy_health_bonus import bonus_note, bonus_potential, fetch_current_stats, fetch_unavailable, norm
from schedule_fallback import fantacalcio_schedule

BASE = Path(__file__).resolve().parent


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


def _roster_names() -> tuple[str, ...]:
    try:
        with (BASE / "roster.csv").open("r", encoding="utf-8", newline="") as f:
            return tuple(row["name"].strip() for row in csv.DictReader(f) if row.get("name"))
    except Exception:
        return tuple()


@st.cache_data(ttl=900, show_spinner=False)
def _editorial_context(names: tuple[str, ...]):
    roster = list(names)
    return fetch_unavailable(roster), fetch_current_stats(roster)


ROSTER_NAMES = _roster_names()
EDITORIAL_UNAVAILABLE, CURRENT_STATS = _editorial_context(ROSTER_NAMES)


# ---------------------------------------------------------------------------
# API-Football Free: ID giocatore da /players/profiles, senza season corrente.
# ---------------------------------------------------------------------------
def _profile_row(player: dict) -> dict:
    return {"player": player or {}, "statistics": []}


def _search_player_profile_free(self, query: str, season: int | None = None, team_hint: str = ""):
    token = (query or "").split()[-1].replace("'", "").strip()
    if len(token) < 3:
        return None, 0
    result = self._get("/players/profiles", {"search": token}, ttl=30 * 24 * 3600)
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
    result = self._get("/players/profiles", {"player": int(player_id)}, ttl=30 * 24 * 3600)
    if not result.response:
        return None
    item = result.response[0]
    return _profile_row(item.get("player") or item)


api_football.APIFootball.search_player_stats = _search_player_profile_free
api_football.APIFootball.player_stats = _player_profile_free


# ---------------------------------------------------------------------------
# Calendario Serie A: Fantacalcio.it come fonte primaria sul piano API Free.
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
# Valutazione: calendario per nome + infortuni/squalifiche + bonus potential.
# ---------------------------------------------------------------------------
_original_evaluate_player = fantasy_engine.evaluate_player


def _evaluate_player_v34(*args, **kwargs):
    ev = _original_evaluate_player(*args, **kwargs)

    # Fixture editoriali: ricava avversario/casa-trasferta dal nome squadra.
    fixture = kwargs.get("fixture")
    if fixture:
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

    key = norm(kwargs.get("name") or ev.name)
    stats = CURRENT_STATS.get(key, {})
    set_piece = kwargs.get("set_piece_role") or {}
    bscore = bonus_potential(ev.role, stats, set_piece)
    setattr(ev, "bonus_score", round(bscore, 1))
    bnote = bonus_note(bscore, stats, set_piece)

    # Il bonus incide di piu' sui ruoli offensivi. L'algoritmo originale contiene
    # gia' una piccola quota threat/piazzati: qui aggiungiamo solo il delta utile.
    bonus_weight = {"P": 0.02, "D": 0.10, "C": 0.18, "A": 0.22}.get(ev.role, 0.12)
    delta = (bscore - 50.0) * bonus_weight
    ev.schierabilita = round(fantasy_engine.clamp(ev.schierabilita + delta), 1)
    ev.label = fantasy_engine.status_label(ev.schierabilita)
    ev.reason = (ev.reason + "; " if ev.reason else "") + bnote
    ev.source_detail = (ev.source_detail + " | " if ev.source_detail else "") + f"Bonus {bscore:.0f}/100"

    # Infortunio/squalifica editoriale = veto forte, prevale su probabili e bonus.
    unavailable = EDITORIAL_UNAVAILABLE.get(key)
    if unavailable:
        status = unavailable.status.lower()
        reason = unavailable.reason.strip() or "segnalato indisponibile da Fantacalcio.it"
        ev.unavailable = f"{status}: {reason}"
        if status == "squalificato":
            ev.p_starter = 0.0
            ev.p_vote = 0.0
            ev.schierabilita = 1.0
        else:
            ev.p_starter = min(ev.p_starter, 1.0)
            ev.p_vote = min(ev.p_vote, 2.0)
            ev.schierabilita = min(ev.schierabilita, 5.0)
        ev.label = "⛔ Da evitare"
        ev.reason = f"⛔ {status.upper()} — {reason}; " + ev.reason
        ev.source_detail = (ev.source_detail + " | " if ev.source_detail else "") + f"Indisponibili Fantacalcio.it: {status}"

    return ev


fantasy_engine.evaluate_player = _evaluate_player_v34


# ---------------------------------------------------------------------------
# Modulo: XI SEMPRE 3-4-3. Alternativa solo come consiglio separato.
# ---------------------------------------------------------------------------
_original_best_lineup = fantasy_engine.best_lineup


def _pick_formation(players, formation: str):
    needs = fantasy_engine.FORMATIONS[formation]
    selector = getattr(fantasy_engine, "_selection_value", lambda p: p.schierabilita)
    chosen = []
    for role, n in needs.items():
        pool = sorted(
            [p for p in players if p.role == role],
            key=lambda p: (selector(p), p.p_vote, getattr(p, "bonus_score", 50), p.schierabilita, p.p_starter),
            reverse=True,
        )
        if len(pool) < n:
            return None
        chosen.extend(pool[:n])

    return {
        "formation": formation,
        "players": chosen,
        "total": sum(p.schierabilita for p in chosen),
        "optimizer_score": sum(selector(p) for p in chosen),
        "vote_floor": min(p.p_vote for p in chosen),
        "expected_votes": sum(p.p_vote for p in chosen) / 100.0,
    }


def _player_diff(base_players, alt_players):
    base = {p.name: p for p in base_players}
    alt = {p.name: p for p in alt_players}
    return (
        [p for name, p in base.items() if name not in alt],
        [p for name, p in alt.items() if name not in base],
    )


def _fmt_players(players):
    if not players:
        return "—"
    return ", ".join(
        f"{p.name.title()} ({p.role}, voto {p.p_vote:.0f}%, bonus {getattr(p, 'bonus_score', 50):.0f}/100)"
        for p in players
    )


def _best_lineup_343_always(players):
    base = _pick_formation(players, "3-4-3")
    if base is None:
        return _original_best_lineup(players)

    alt = _original_best_lineup(players)
    if alt and alt.get("formation") != "3-4-3":
        alt_players = alt.get("players", [])
        alt_expected = sum(p.p_vote for p in alt_players) / 100.0 if alt_players else 0.0
        alt_floor = min((p.p_vote for p in alt_players), default=0.0)
        gain_expected = alt_expected - base["expected_votes"]
        gain_floor = alt_floor - base["vote_floor"]
        out_players, in_players = _player_diff(base["players"], alt_players)

        if gain_expected >= 0.25 or gain_floor >= 12:
            reasons = []
            if gain_expected >= 0.25:
                reasons.append(f"circa {gain_expected:.2f} voti attesi in piu' sull'XI")
            if gain_floor >= 12:
                reasons.append(
                    f"il giocatore piu' a rischio passa da {base['vote_floor']:.0f}% a {alt_floor:.0f}% di probabilita' voto"
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
