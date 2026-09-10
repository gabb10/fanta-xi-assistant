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

TEAM_NAMES = {
    "ATALANTA", "BOLOGNA", "CAGLIARI", "COMO", "CREMONESE", "FIORENTINA",
    "FROSINONE", "GENOA", "INTER", "JUVENTUS", "LAZIO", "LECCE", "MILAN",
    "MONZA", "NAPOLI", "PARMA", "PISA", "ROMA", "SASSUOLO", "TORINO",
    "UDINESE", "VENEZIA", "VERONA", "HELLAS VERONA",
}


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.upper().replace("-", " ").replace(".", " ").replace("'", " ")
    return " ".join(value.split())


def _get_html(url: str) -> str:
    r = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; FantaXIAssistant/3.4; personal-use)"},
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
    """Legge infortunati e squalificati editoriali.

    Lo status e' intenzionalmente forte: serve da veto rispetto a una probabile
    formazione eventualmente non ancora allineata.
    """
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
            # Copre casi tipo Pio Esposito / Esposito P. senza rischiare match larghi.
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

        # Dopo il nome la tabella standard e': Sq, PV, MV, FM, Gol, GS, Rig, RP, Ass, Amm, Esp.
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


def bonus_potential(role: str, stats: dict[str, Any] | None, set_piece: dict[str, Any] | None):
    """Indice 0-100 di potenziale bonus, prudente a inizio stagione."""
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
        # Campione piccolo: nelle prime giornate non facciamo dominare 1 gol isolato.
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

    return max(0.0, min(100.0, score))


def bonus_note(score: float, stats: dict[str, Any] | None, set_piece: dict[str, Any] | None):
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
    return "; ".join(bits)
