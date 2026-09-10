from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, asdict
from typing import Any

import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

UNAVAILABLE_URL = "https://www.fantacalcio.it/serie-a/indisponibili"
INJURED_URL = "https://www.fantacalcio.it/infortunati-serie-a"
STATS_URL = "https://www.fantacalcio.it/statistiche-serie-a/2026-27/fantacalcio/riepilogo"
STANDINGS_URL = "https://www.fantacalcio.it/serie-a/calendario/27"

TEAM_NAMES = {
    "ATALANTA", "BOLOGNA", "CAGLIARI", "COMO", "CREMONESE", "FIORENTINA",
    "FROSINONE", "GENOA", "INTER", "JUVENTUS", "LAZIO", "LECCE", "MILAN",
    "MONZA", "NAPOLI", "PARMA", "PISA", "ROMA", "SASSUOLO", "TORINO",
    "UDINESE", "VENEZIA", "VERONA", "HELLAS VERONA",
}

TEAM_ALIASES = {
    "HELLAS VERONA": "VERONA",
    "INTERNAZIONALE": "INTER",
    "FC INTERNAZIONALE MILANO": "INTER",
    "AC MILAN": "MILAN",
}


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.upper().replace("-", " ").replace(".", " ").replace("'", " ")
    value = " ".join(value.split())
    return TEAM_ALIASES.get(value, value)


def _get_html(url: str) -> str:
    r = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; FantaXIAssistant/3.4.1; personal-use)"},
        timeout=18,
    )
    r.raise_for_status()
    return r.text


def _best_roster_match(text: str, roster_names: list[str]):
    nt = norm(text)
    best, score = None, 0.0
    for name in roster_names:
        nn = norm(name)
        s = max(fuzz.ratio(nt, nn), fuzz.token_set_ratio(nt, nn))
        if nt == nn:
            s = 100
        if s > score:
            best, score = name, s
    return best, score


@dataclass
class Availability:
    status: str
    reason: str
    team: str = ""
    source: str = "Fantacalcio.it"

    def to_dict(self):
        return asdict(self)


def fetch_unavailable(roster_names: list[str]) -> dict[str, Availability]:
    """Legge infortunati e squalificati editoriali con priorita' forte."""
    out: dict[str, Availability] = {}
    try:
        html = _get_html(UNAVAILABLE_URL)
        soup = BeautifulSoup(html, "html.parser")
        lines = [x.strip() for x in soup.get_text("\n").splitlines() if x.strip()]
    except Exception:
        try:
            html = _get_html(INJURED_URL)
            soup = BeautifulSoup(html, "html.parser")
            lines = [x.strip() for x in soup.get_text("\n").splitlines() if x.strip()]
        except Exception:
            return out

    current_team = ""
    current_section = ""
    roster_norm = {norm(x): x for x in roster_names}

    for i, line in enumerate(lines):
        n = norm(line)
        if n in TEAM_NAMES:
            current_team = line
            continue
        if n in {"INFORTUNATI", "SQUALIFICATI", "DIFFIDATI"}:
            current_section = n
            continue
        if current_section not in {"INFORTUNATI", "SQUALIFICATI"}:
            continue

        matched_name = roster_norm.get(n)
        if not matched_name:
            candidate, score = _best_roster_match(line, roster_names)
            if score < 92:
                continue
            matched_name = candidate

        reason = ""
        for nxt in lines[i + 1:i + 4]:
            nn = norm(nxt)
            if nn in TEAM_NAMES or nn in {"INFORTUNATI", "SQUALIFICATI", "DIFFIDATI"}:
                break
            if len(nxt) > 12:
                reason = nxt
                break
        status = "squalificato" if current_section == "SQUALIFICATI" else "infortunato"
        out[norm(matched_name)] = Availability(status=status, reason=reason, team=current_team)

    return out


def _num(value: str | None):
    if value is None:
        return None
    s = str(value).strip().replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def fetch_current_stats(roster_names: list[str]) -> dict[str, dict[str, Any]]:
    """Una sola pagina per PV/MV/FM/Gol/Assist dell'intera Serie A."""
    out: dict[str, dict[str, Any]] = {}
    try:
        soup = BeautifulSoup(_get_html(STATS_URL), "html.parser")
    except Exception:
        return out

    for tr in soup.find_all("tr"):
        cells = [x.get_text(" ", strip=True) for x in tr.find_all(["td", "th"])]
        if len(cells) < 8:
            continue

        best_name, best_score, name_idx = None, 0.0, None
        for idx, cell in enumerate(cells[:6]):
            candidate, score = _best_roster_match(cell, roster_names)
            if score > best_score:
                best_name, best_score, name_idx = candidate, score, idx
        if not best_name or best_score < 88 or name_idx is None:
            continue

        tail = cells[name_idx + 1:]
        if len(tail) < 8:
            continue
        team = tail[0]
        pv = _num(tail[1])
        mv = _num(tail[2])
        fm = _num(tail[3])
        goals = _num(tail[4]) or 0.0
        rig = tail[6] if len(tail) > 6 else ""
        assists = _num(tail[8]) if len(tail) > 8 else 0.0
        assists = assists or 0.0

        out[norm(best_name)] = {
            "team": team,
            "pv": pv or 0.0,
            "mv": mv,
            "fm": fm,
            "goals": goals,
            "assists": assists,
            "rig": rig,
        }
    return out


def fetch_team_table() -> dict[str, dict[str, float]]:
    """Classifica live: G, Pt, GF, GS.

    Usata solo per il contesto matchup. Il parser cerca una riga contenente una
    squadra Serie A e poi usa la sequenza numerica standard Pt, G, V, P, S, GF,
    GS, DR. Se la pagina cambia struttura restituisce semplicemente un dict vuoto.
    """
    out: dict[str, dict[str, float]] = {}
    try:
        soup = BeautifulSoup(_get_html(STANDINGS_URL), "html.parser")
    except Exception:
        return out

    for tr in soup.find_all("tr"):
        cells = [x.get_text(" ", strip=True) for x in tr.find_all(["td", "th"])]
        if len(cells) < 7:
            continue

        team = None
        team_idx = None
        for i, cell in enumerate(cells[:4]):
            nc = norm(cell)
            if nc in TEAM_NAMES:
                team, team_idx = nc, i
                break
            for candidate in TEAM_NAMES:
                if candidate and candidate in nc and len(candidate) >= 4:
                    team, team_idx = candidate, i
                    break
            if team:
                break
        if team is None or team_idx is None:
            continue

        nums = []
        for cell in cells[team_idx + 1:]:
            n = _num(cell)
            if n is not None:
                nums.append(n)
        if len(nums) < 7:
            continue

        # Standard Fantacalcio: Pt, G, V, P, S, GF, GS, DR
        pt, games, wins, draws, losses, gf, gs = nums[:7]
        if games <= 0 or games > 38 or gf < 0 or gs < 0:
            continue
        out[norm(team)] = {
            "points": pt,
            "games": games,
            "gf": gf,
            "gs": gs,
        }
    return out


def matchup_context(team: str, opponent: str, home_away: str,
                    table: dict[str, dict[str, float]] | None):
    """Ritorna score matchup 0-100 e moltiplicatore prudente circa 0.82-1.20.

    Considera capacita' realizzativa della squadra, gol subiti dall'avversario e
    casa/trasferta. I dati iniziali vengono fortemente shrinkati verso la media.
    """
    table = table or {}
    t = table.get(norm(team))
    o = table.get(norm(opponent))
    home = norm(home_away) == "CASA"
    away = norm(home_away) == "TRASFERTA"

    if not t or not o:
        score = 54.0 if home else 46.0 if away else 50.0
        factor = 1.04 if home else 0.96 if away else 1.0
        return score, factor, "solo fattore casa/trasferta"

    rows = [x for x in table.values() if x.get("games", 0) > 0]
    total_games = sum(x["games"] for x in rows)
    league_gfpg = (sum(x["gf"] for x in rows) / total_games) if total_games else 1.35
    league_gfpg = max(0.8, league_gfpg)

    tg = max(1.0, t["games"])
    og = max(1.0, o["games"])
    raw_attack = (t["gf"] / tg) / league_gfpg
    raw_opp_generosity = (o["gs"] / og) / league_gfpg

    # Con 3 giornate il peso dati e' 3/(3+6)=33%: molto prudente.
    wt = tg / (tg + 6.0)
    wo = og / (og + 6.0)
    attack = 1.0 + (raw_attack - 1.0) * wt
    generosity = 1.0 + (raw_opp_generosity - 1.0) * wo

    # La difficolta' generale dell'avversario entra debolmente tramite punti/partita.
    all_ppg = [x["points"] / max(1.0, x["games"]) for x in rows]
    avg_ppg = sum(all_ppg) / len(all_ppg) if all_ppg else 1.35
    opp_ppg = o["points"] / og
    strength_penalty = max(-0.08, min(0.08, (opp_ppg - avg_ppg) * 0.035))

    loc = 1.07 if home else 0.93 if away else 1.0
    raw_factor = (0.50 * attack + 0.50 * generosity) * loc * (1.0 - strength_penalty)
    factor = max(0.82, min(1.20, raw_factor))
    score = max(0.0, min(100.0, 50.0 + (factor - 1.0) * 220.0))

    note = (
        f"GF squadra {t['gf']:.0f}/{t['games']:.0f}, "
        f"GS avversario {o['gs']:.0f}/{o['games']:.0f}, "
        f"{'casa' if home else 'trasferta' if away else 'campo neutro'}"
    )
    return score, factor, note


def bonus_potential(role: str, stats: dict[str, Any] | None, set_piece: dict[str, Any] | None,
                    matchup_score: float | None = None):
    """Indice 0-100 di potenziale bonus individuale + contesto partita."""
    role = (role or "").upper()
    stats = stats or {}
    set_piece = set_piece or {}

    base = {"P": 12.0, "D": 27.0, "C": 49.0, "A": 56.0}.get(role, 42.0)
    pv = float(stats.get("pv") or 0.0)
    goals = float(stats.get("goals") or 0.0)
    assists = float(stats.get("assists") or 0.0)
    mv = stats.get("mv")
    fm = stats.get("fm")

    empirical = base
    if pv > 0:
        gp90 = goals / pv
        ap90 = assists / pv
        fanta_delta = max(0.0, float(fm or 0) - float(mv or fm or 0)) if fm is not None else 0.0
        empirical = base + 34 * gp90 + 17 * ap90 + 5.5 * fanta_delta
        w = min(0.60, pv / 8.0)
        score = base * (1 - w) + empirical * w
    else:
        score = base

    pen_rank = set_piece.get("penalty_rank")
    set_rank = set_piece.get("set_piece_rank")
    if pen_rank == 1:
        score += 20
    elif pen_rank == 2:
        score += 11
    elif pen_rank == 3:
        score += 5
    if set_rank == 1:
        score += 8
    elif set_rank == 2:
        score += 5
    elif set_rank == 3:
        score += 2

    # Il contesto della singola partita pesa soprattutto sui giocatori da bonus.
    if matchup_score is not None and role != "P":
        sensitivity = {"D": 0.18, "C": 0.32, "A": 0.45}.get(role, 0.25)
        score += (float(matchup_score) - 50.0) * sensitivity

    return max(0.0, min(100.0, score))


def bonus_note(score: float, stats: dict[str, Any] | None, set_piece: dict[str, Any] | None,
               matchup_score: float | None = None, matchup_note: str = ""):
    stats = stats or {}
    set_piece = set_piece or {}
    bits = [f"bonus potenziale {score:.0f}/100"]
    pv = int(stats.get("pv") or 0)
    if pv:
        bits.append(f"{int(stats.get('goals') or 0)} gol, {int(stats.get('assists') or 0)} assist in {pv} voti")
    if set_piece.get("penalty_rank") == 1:
        bits.append("1° rigorista")
    elif set_piece.get("penalty_rank") == 2:
        bits.append("2° rigorista")
    if set_piece.get("set_piece_rank") == 1:
        bits.append("1° sui piazzati")
    if matchup_score is not None:
        label = "molto favorevole" if matchup_score >= 68 else "favorevole" if matchup_score >= 57 else "difficile" if matchup_score <= 43 else "molto difficile" if matchup_score <= 32 else "neutro"
        bits.append(f"matchup {label} {matchup_score:.0f}/100")
        if matchup_note:
            bits.append(matchup_note)
    return "; ".join(bits)
