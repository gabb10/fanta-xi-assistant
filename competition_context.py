from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any

SERIE_A_ID = 135
ROME = ZoneInfo("Europe/Rome")
FINISHED = {"FT", "AET", "PEN"}
SKIP_STATUSES = {"PST", "CANC", "SUSP"}
FRIENDLY_WORDS = ("friendly", "friendlies", "amichevol")


def _dt(raw: str | None):
    if not raw:
        return None
    try:
        d = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=ROME)
        return d.astimezone(ROME)
    except Exception:
        return None


def format_kickoff(raw: str | None) -> str:
    d = _dt(raw)
    if not d:
        return "data/ora da definire"
    weekdays = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
    months = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]
    return f"{weekdays[d.weekday()]} {d.day} {months[d.month-1]} · {d:%H:%M}"


def next_serie_a_fixtures(api, season: int, count: int = 30):
    return api._get(
        "/fixtures",
        {"league": SERIE_A_ID, "season": season, "next": count, "timezone": "Europe/Rome"},
        ttl=60 * 60,
    ).response


def _norm_team(name: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.upper().replace("-", " ").replace(".", " ").replace("'", " ")
    aliases = {
        "AC MILAN": "MILAN",
        "FC INTERNAZIONALE MILANO": "INTER",
        "INTERNAZIONALE": "INTER",
        "HELLAS VERONA": "VERONA",
    }
    s = " ".join(s.split())
    return aliases.get(s, s)


def find_serie_a_fixture(fixtures: list[dict[str, Any]], team_name: str = "", team_id: int | None = None):
    target = _norm_team(team_name)
    for fx in fixtures:
        teams = fx.get("teams") or {}
        home, away = teams.get("home") or {}, teams.get("away") or {}
        if team_id and team_id in (home.get("id"), away.get("id")):
            return fx
        names = {_norm_team(home.get("name", "")), _norm_team(away.get("name", ""))}
        if target and target in names:
            return fx
    return None


def team_id_from_fixture(fixture: dict[str, Any] | None, team_name: str = "", fallback_id: int | None = None):
    if not fixture:
        return fallback_id
    target = _norm_team(team_name)
    teams = fixture.get("teams") or {}
    for side in ("home", "away"):
        team = teams.get(side) or {}
        if fallback_id and team.get("id") == fallback_id:
            return fallback_id
        if target and _norm_team(team.get("name", "")) == target:
            return team.get("id")
    return fallback_id


def opponent_from_fixture(fixture: dict[str, Any] | None, team_name: str = "", team_id: int | None = None):
    if not fixture:
        return "—", "—"
    target = _norm_team(team_name)
    teams = fixture.get("teams") or {}
    home, away = teams.get("home") or {}, teams.get("away") or {}
    if team_id == home.get("id") or (target and _norm_team(home.get("name", "")) == target):
        return away.get("name", "—"), "Casa"
    if team_id == away.get("id") or (target and _norm_team(away.get("name", "")) == target):
        return home.get("name", "—"), "Trasferta"
    return "—", "—"


def _is_cup_fixture(fx: dict[str, Any]) -> bool:
    league = fx.get("league") or {}
    if league.get("id") == SERIE_A_ID or str(league.get("name", "")).lower() == "serie a":
        return False
    lname = str(league.get("name", "")).lower()
    if any(x in lname for x in FRIENDLY_WORDS):
        return False
    status = ((fx.get("fixture") or {}).get("status") or {}).get("short")
    if status in SKIP_STATUSES:
        return False
    return True


def cup_window(api, team_id: int | None, league_fixture: dict[str, Any] | None, days: int = 5):
    if not team_id or not league_fixture:
        return {"before": None, "after": None, "all": []}
    center = _dt((league_fixture.get("fixture") or {}).get("date"))
    if not center:
        return {"before": None, "after": None, "all": []}
    start = (center - timedelta(days=days)).date().isoformat()
    end = (center + timedelta(days=days)).date().isoformat()
    try:
        rows = api._get(
            "/fixtures",
            {"team": int(team_id), "from": start, "to": end, "timezone": "Europe/Rome"},
            ttl=8 * 3600,
        ).response
    except Exception:
        return {"before": None, "after": None, "all": []}

    cups = []
    league_fixture_id = (league_fixture.get("fixture") or {}).get("id")
    for fx in rows:
        if (fx.get("fixture") or {}).get("id") == league_fixture_id or not _is_cup_fixture(fx):
            continue
        d = _dt((fx.get("fixture") or {}).get("date"))
        if not d:
            continue
        delta_hours = (d - center).total_seconds() / 3600
        cups.append((delta_hours, fx))
    before = [x for x in cups if x[0] < 0]
    after = [x for x in cups if x[0] > 0]
    return {
        "before": max(before, key=lambda x: x[0])[1] if before else None,
        "after": min(after, key=lambda x: x[0])[1] if after else None,
        "all": [x[1] for x in sorted(cups, key=lambda x: x[0])],
    }


def player_minutes_in_fixture(api, fixture_id: int, player_id: int | None):
    if not fixture_id or not player_id:
        return None
    try:
        blocks = api._get("/fixtures/players", {"fixture": int(fixture_id)}, ttl=12 * 3600).response
    except Exception:
        return None
    for block in blocks:
        for item in block.get("players") or []:
            if (item.get("player") or {}).get("id") != player_id:
                continue
            stats = (item.get("statistics") or [{}])[0]
            games = stats.get("games") or {}
            mins = games.get("minutes")
            return 0 if mins is None else int(mins)
    return 0


def cup_adjustment(api, window: dict[str, Any], league_fixture: dict[str, Any] | None, player_id: int | None):
    if not league_fixture:
        return 0.0, ""
    center = _dt((league_fixture.get("fixture") or {}).get("date"))
    if not center:
        return 0.0, ""

    adjustment = 0.0
    notes = []
    now = datetime.now(ROME)

    before = window.get("before")
    if before:
        bd = _dt((before.get("fixture") or {}).get("date"))
        gap_h = (center - bd).total_seconds() / 3600 if bd else 999
        comp = (before.get("league") or {}).get("name") or "Coppa"
        status = ((before.get("fixture") or {}).get("status") or {}).get("short")
        mins = None
        if bd and bd <= now and status in FINISHED and player_id:
            mins = player_minutes_in_fixture(api, (before.get("fixture") or {}).get("id"), player_id)

        if mins is not None:
            if mins >= 75:
                adjustment -= 9 if gap_h <= 96 else 5
                notes.append(f"{mins}' in {comp} {round(gap_h/24)} gg prima")
            elif mins >= 45:
                adjustment -= 6 if gap_h <= 96 else 3
                notes.append(f"{mins}' in {comp} {round(gap_h/24)} gg prima")
            elif mins > 0:
                adjustment -= 2
                notes.append(f"{mins}' in {comp} {round(gap_h/24)} gg prima")
            else:
                adjustment += 2
                notes.append(f"riposato in {comp} {round(gap_h/24)} gg prima")
        else:
            adjustment -= 4 if gap_h <= 96 else 2
            notes.append(f"{comp} {round(gap_h/24)} gg prima")

    after = window.get("after")
    if after:
        ad = _dt((after.get("fixture") or {}).get("date"))
        gap_h = (ad - center).total_seconds() / 3600 if ad else 999
        comp = (after.get("league") or {}).get("name") or "Coppa"
        adjustment -= 4 if gap_h <= 72 else 2 if gap_h <= 96 else 1
        notes.append(f"{comp} {round(gap_h/24)} gg dopo")

    if before and after:
        adjustment -= 1.5
        notes.append("calendario molto congestionato")

    return max(-14.0, min(3.0, adjustment)), "; ".join(notes)
