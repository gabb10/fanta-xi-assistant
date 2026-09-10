from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from competition_context import next_serie_a_fixtures as _api_next_serie_a_fixtures

FANTACALCIO_PROBABILI = "https://www.fantacalcio.it/probabili-formazioni-serie-a"
ROME = ZoneInfo("Europe/Rome")

MONTHS = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4,
    "maggio": 5, "giugno": 6, "luglio": 7, "agosto": 8,
    "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}

TEAM_DISPLAY = {
    "ATALANTA": "Atalanta", "BOLOGNA": "Bologna", "CAGLIARI": "Cagliari",
    "COMO": "Como", "CREMONESE": "Cremonese", "FIORENTINA": "Fiorentina",
    "FROSINONE": "Frosinone", "GENOA": "Genoa", "INTER": "Inter",
    "INTERNAZIONALE": "Inter", "JUVENTUS": "Juventus", "LAZIO": "Lazio",
    "LECCE": "Lecce", "MILAN": "Milan", "MONZA": "Monza", "NAPOLI": "Napoli",
    "PARMA": "Parma", "PISA": "Pisa", "ROMA": "Roma", "SASSUOLO": "Sassuolo",
    "TORINO": "Torino", "UDINESE": "Udinese", "VENEZIA": "Venezia",
    "VERONA": "Verona", "HELLAS VERONA": "Verona",
}


def _norm(value: str) -> str:
    import unicodedata
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.upper().replace("-", " ").replace(".", " ").replace("'", " ")
    return " ".join(value.split())


def _date_from_line(line: str, season: int):
    # Esempio reale Fantacalcio.it: "venerdì 11 settembre, 20:45"
    m = re.search(
        r"\b(\d{1,2})\s+(gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre)\s*,\s*(\d{1,2}):(\d{2})\b",
        line.lower(),
    )
    if not m:
        return None
    day = int(m.group(1))
    month = MONTHS[m.group(2)]
    hour = int(m.group(3))
    minute = int(m.group(4))
    # API-Football usa l'anno di inizio stagione: season=2026 => 2026/27.
    year = season if month >= 7 else season + 1
    try:
        return datetime(year, month, day, hour, minute, tzinfo=ROME)
    except ValueError:
        return None


def fantacalcio_schedule(season: int) -> list[dict]:
    """Legge la giornata corrente dalle probabili Fantacalcio.it.

    Restituisce pseudo-fixture nello stesso formato essenziale di API-Football.
    Viene usato solo come fallback calendario: non inventa ID squadra/fixture.
    """
    try:
        r = requests.get(
            FANTACALCIO_PROBABILI,
            headers={"User-Agent": "Mozilla/5.0 (compatible; FantaXIAssistant/3.3.1; personal-use)"},
            timeout=18,
        )
        r.raise_for_status()
    except Exception:
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    lines = [x.strip() for x in soup.get_text("\n").splitlines() if x.strip()]

    date_points = []
    for idx, line in enumerate(lines):
        dt = _date_from_line(line, season)
        if dt is not None:
            date_points.append((idx, dt))

    fixtures = []
    seen = set()
    for pos, (idx, dt) in enumerate(date_points):
        end = date_points[pos + 1][0] if pos + 1 < len(date_points) else min(len(lines), idx + 420)
        teams = []
        for line in lines[idx + 1:end]:
            key = _norm(line)
            if key in TEAM_DISPLAY:
                name = TEAM_DISPLAY[key]
                if name not in teams:
                    teams.append(name)
                if len(teams) == 2:
                    break
        if len(teams) != 2:
            continue

        home, away = teams
        match_key = (_norm(home), _norm(away), dt.isoformat())
        if match_key in seen:
            continue
        seen.add(match_key)
        fixtures.append({
            "fixture": {
                "id": None,
                "date": dt.isoformat(),
                "status": {"short": "NS"},
            },
            "league": {
                "id": 135,
                "name": "Serie A",
                "season": season,
                "source": "Fantacalcio.it",
            },
            "teams": {
                "home": {"id": None, "name": home},
                "away": {"id": None, "name": away},
            },
        })

    fixtures.sort(key=lambda fx: (fx.get("fixture") or {}).get("date", ""))
    return fixtures


def _match_key(fx: dict):
    teams = fx.get("teams") or {}
    h = _norm(((teams.get("home") or {}).get("name")) or "")
    a = _norm(((teams.get("away") or {}).get("name")) or "")
    return h, a


def next_serie_a_fixtures_with_fallback(api, season: int, count: int = 30):
    """API-Football quando disponibile + calendario Fantacalcio come fallback."""
    try:
        api_rows = _api_next_serie_a_fixtures(api, season, count) or []
    except Exception:
        api_rows = []

    fallback_rows = fantacalcio_schedule(season)
    if not api_rows:
        return fallback_rows
    if not fallback_rows:
        return api_rows

    # Mantiene le fixture API (hanno ID utili), ma aggiunge la giornata editoriale
    # se il piano/API non l'ha restituita. Ordinando per data, find_serie_a_fixture
    # prenderà la gara imminente corretta.
    keys = {_match_key(x) for x in api_rows}
    merged = list(api_rows)
    for fx in fallback_rows:
        if _match_key(fx) not in keys:
            merged.append(fx)

    merged.sort(key=lambda fx: (fx.get("fixture") or {}).get("date", "9999"))
    return merged
