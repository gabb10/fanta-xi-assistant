from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

PROBABILI_URL = "https://www.fantacalcio.it/probabili-formazioni-serie-a"
RIGORISTI_URL = "https://www.fantacalcio.it/rigoristi-serie-a"

TEAM_NAMES = {
    "ATALANTA", "BOLOGNA", "CAGLIARI", "COMO", "FIORENTINA", "FROSINONE",
    "GENOA", "INTER", "JUVENTUS", "LAZIO", "LECCE", "MILAN", "MONZA",
    "NAPOLI", "PARMA", "ROMA", "SASSUOLO", "TORINO", "UDINESE", "VENEZIA",
    "CREMONESE", "PISA", "VERONA",
}

# Hint espliciti per la rosa dell'utente, stagione 2026/27. Servono a evitare
# errori dovuti all'ordine del testo nelle pagine delle probabili formazioni.
ROSTER_TEAM_HINTS = {
    "MAIGNAN": "Milan",
    "TERRACCIANO": "Milan",
    "TORRIANI": "Milan",
    "MOLINA": "Roma",
    "MIRANDA": "Bologna",
    "HAPS": "Venezia",
    "LULLI": "Roma",
    "CAMBIASO": "Juventus",
    "BADIASHILE": "Napoli",
    "BELGHALI": "Torino",
    "MAZZOCCHI": "Venezia",
    "ORSOLINI": "Bologna",
    "MASTANTUONO": "Fiorentina",
    "CONCEICAO": "Juventus",
    "KESSIE": "Atalanta",
    "CALO": "Frosinone",
    "ROMANO": "Cagliari",
    "FITZ JIM": "Torino",
    "DOUGLAS LUIZ": "Juventus",
    "PINAMONTI": "Sassuolo",
    "SCAMACCA": "Atalanta",
    "PIO ESPOSITO": "Inter",
    "GEUBBELS": "Lecce",
    "MALDINI": "Cagliari",
    "BONNY": "Inter",
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.upper().replace("-", " ").replace(".", " ").replace("'", " ")
    return " ".join(s.split())


def _fetch_lines(url: str) -> list[str]:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; FantaXIAssistant/3.2; personal-use)"}
    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    return [x.strip() for x in soup.get_text("\n").splitlines() if x.strip()]


def _best_name_match(target: str, candidates: dict[str, Any]):
    nt = norm(target)
    best_key, best_score = None, 0
    for key in candidates:
        score = max(
            fuzz.ratio(nt, key),
            fuzz.partial_ratio(nt, key) if min(len(nt), len(key)) >= 5 else 0,
            fuzz.token_set_ratio(nt, key),
        )
        if score > best_score:
            best_key, best_score = key, score
    return best_key, best_score


@dataclass
class ProbableSource:
    percentages: dict[str, float]
    matched_names: dict[str, str]
    teams: dict[str, str]
    updated_at: str
    ok: bool
    error: str = ""


def fetch_probable_percentages(roster_names: list[str]) -> ProbableSource:
    try:
        lines = _fetch_lines(PROBABILI_URL)
        raw: dict[str, float] = {}
        raw_team: dict[str, str] = {}
        current_team = ""

        for i, line in enumerate(lines[:-1]):
            n = norm(line)
            if n in TEAM_NAMES:
                current_team = line
                continue

            m = re.fullmatch(r"(\d{1,3})\s*%", lines[i + 1])
            if not m:
                continue
            if len(line) > 45 or line.lower() in {"panchina", "rigori", "calci piazzati"}:
                continue
            pct = float(m.group(1))
            if 0 <= pct <= 100:
                key = norm(line)
                raw[key] = pct
                if current_team:
                    raw_team[key] = current_team

        for line in lines:
            m = re.fullmatch(r"(.{2,45}?)\s+(\d{1,3})\s*%", line)
            if m:
                raw[norm(m.group(1))] = float(m.group(2))

        out, matched, teams = {}, {}, {}
        for name in roster_names:
            key, score = _best_name_match(name, raw)
            nk = norm(name)
            if key and score >= 72:
                out[nk] = raw[key]
                matched[nk] = key
            # Per la squadra preferiamo sempre l'hint esplicito della rosa.
            if ROSTER_TEAM_HINTS.get(nk):
                teams[nk] = ROSTER_TEAM_HINTS[nk]
            elif key and raw_team.get(key):
                teams[nk] = raw_team[key]

        stamps = re.findall(
            r"Ultimo aggiornamento\s+(\d{2}/\d{2}/\d{4}\s*-\s*\d{2}:\d{2})",
            " ".join(lines), flags=re.I,
        )
        updated = ""
        if stamps:
            parsed = []
            for stamp in stamps:
                try:
                    parsed.append(datetime.strptime(stamp.replace(" ", ""), "%d/%m/%Y-%H:%M"))
                except ValueError:
                    pass
            if parsed:
                updated = max(parsed).strftime("%d/%m/%Y %H:%M")

        return ProbableSource(out, matched, teams, updated, True)
    except Exception as exc:
        # Anche se Fantacalcio.it non risponde, manteniamo gli hint squadra.
        teams = {norm(n): ROSTER_TEAM_HINTS[norm(n)] for n in roster_names if norm(n) in ROSTER_TEAM_HINTS}
        return ProbableSource({}, {}, teams, "", False, str(exc))


@dataclass
class SetPieceSource:
    roles: dict[str, dict[str, Any]]
    ok: bool
    error: str = ""


def fetch_set_piece_roles(roster_names: list[str]) -> SetPieceSource:
    try:
        lines = _fetch_lines(RIGORISTI_URL)
        candidate_roles: dict[str, dict[str, Any]] = {}
        current_team = ""
        current_section = ""

        def add_player(player: str, rank: int):
            key = norm(player)
            if not key or len(key) < 2:
                return
            rec = candidate_roles.setdefault(
                key,
                {"penalty_rank": None, "set_piece_rank": None, "team": current_team},
            )
            if current_section == "RIGORI":
                rec["penalty_rank"] = rank
            elif current_section == "CALCI PIAZZATI":
                rec["set_piece_rank"] = rank
            if current_team:
                rec["team"] = current_team

        i = 0
        while i < len(lines):
            line = lines[i]
            n = norm(line)
            if n in TEAM_NAMES:
                current_team = line
                current_section = ""
                i += 1
                continue
            if n == "RIGORI":
                current_section = "RIGORI"
                i += 1
                continue
            if n == "CALCI PIAZZATI":
                current_section = "CALCI PIAZZATI"
                i += 1
                continue

            m_rank = re.fullmatch(r"([123])[\.\)]?", line)
            if m_rank and i + 1 < len(lines) and current_section:
                nxt = lines[i + 1]
                if norm(nxt) not in TEAM_NAMES and norm(nxt) not in {"RIGORI", "CALCI PIAZZATI"}:
                    add_player(nxt, int(m_rank.group(1)))
                    i += 2
                    continue

            m_inline = re.fullmatch(r"([123])[\.\)]?\s+(.{2,45})", line)
            if m_inline and current_section:
                add_player(m_inline.group(2), int(m_inline.group(1)))
            i += 1

        out = {}
        for name in roster_names:
            key, score = _best_name_match(name, candidate_roles)
            if key and score >= 75:
                out[norm(name)] = candidate_roles[key]
        return SetPieceSource(out, True)
    except Exception as exc:
        return SetPieceSource({}, False, str(exc))
