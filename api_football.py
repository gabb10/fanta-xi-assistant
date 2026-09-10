from __future__ import annotations

import hashlib
import json
import time
import unicodedata
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from rapidfuzz import fuzz

BASE_URL = "https://v3.football.api-sports.io"


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.upper().replace("-", " ").replace(".", " ").replace("'", " ")
    return " ".join(value.split())


class DiskCache:
    def __init__(self, folder: str = ".fantacache"):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def _path(self, endpoint: str, params: dict[str, Any]) -> Path:
        raw = endpoint + "|" + json.dumps(params, sort_keys=True, ensure_ascii=False)
        return self.folder / f"{hashlib.sha1(raw.encode()).hexdigest()}.json"

    def get(self, endpoint: str, params: dict[str, Any], ttl: int):
        p = self._path(endpoint, params)
        if not p.exists():
            return None
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
            if time.time() - payload["saved_at"] <= ttl:
                return payload["data"]
        except Exception:
            return None
        return None

    def set(self, endpoint: str, params: dict[str, Any], data: Any):
        self._path(endpoint, params).write_text(
            json.dumps({"saved_at": time.time(), "data": data}, ensure_ascii=False),
            encoding="utf-8",
        )

    def clear(self):
        for p in self.folder.glob("*.json"):
            p.unlink(missing_ok=True)


@dataclass
class APIResult:
    response: list[dict[str, Any]]
    errors: Any
    results: int


class APIFootball:
    """API-Football wrapper optimized for the Free plan."""

    def __init__(self, api_key: str, cache_dir: str = ".fantacache"):
        self.api_key = api_key.strip()
        self.session = requests.Session()
        self.session.headers.update({"x-apisports-key": self.api_key})
        self.cache = DiskCache(cache_dir)
        self.calls_this_run = 0
        self.cache_hits = 0
        self.rate_wait_seconds = 0.0
        self.daily_limit: int | None = None
        self.daily_remaining: int | None = None
        self.minute_limit: int | None = None
        self.minute_remaining: int | None = None
        self.last_error = ""
        self._request_times: deque[float] = deque()
        self._safe_minute_cap = 9

    def _update_rate_headers(self, r: requests.Response):
        def as_int(name: str):
            try:
                v = r.headers.get(name)
                return int(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        self.daily_limit = as_int("x-ratelimit-requests-limit") or self.daily_limit
        self.daily_remaining = as_int("x-ratelimit-requests-remaining")
        self.minute_limit = as_int("X-RateLimit-Limit") or self.minute_limit
        self.minute_remaining = as_int("X-RateLimit-Remaining")
        if self.minute_limit:
            self._safe_minute_cap = max(1, self.minute_limit - 1) if self.minute_limit <= 20 else self.minute_limit

    def _wait_for_slot(self):
        now = time.monotonic()
        while self._request_times and now - self._request_times[0] >= 60:
            self._request_times.popleft()
        cap = max(1, self._safe_minute_cap)
        if len(self._request_times) >= cap:
            wait = 60.5 - (now - self._request_times[0])
            if wait > 0:
                self.rate_wait_seconds += wait
                time.sleep(wait)
            now = time.monotonic()
            while self._request_times and now - self._request_times[0] >= 60:
                self._request_times.popleft()

    def _network_get(self, endpoint: str, params: dict[str, Any], retry_429: bool = True) -> requests.Response:
        self._wait_for_slot()
        r = self.session.get(BASE_URL + endpoint, params=params, timeout=25)
        self._request_times.append(time.monotonic())
        self.calls_this_run += 1
        self._update_rate_headers(r)

        if r.status_code == 429 and retry_429:
            try:
                retry_after = float(r.headers.get("Retry-After", "0"))
            except ValueError:
                retry_after = 0
            wait = max(61.0, retry_after)
            self.rate_wait_seconds += wait
            time.sleep(wait)
            return self._network_get(endpoint, params, retry_429=False)
        return r

    def _get(self, endpoint: str, params: dict[str, Any], ttl: int = 0) -> APIResult:
        endpoint = endpoint if endpoint.startswith("/") else "/" + endpoint
        if ttl:
            cached = self.cache.get(endpoint, params, ttl)
            if cached is not None:
                self.cache_hits += 1
                return APIResult(**cached)

        r = self._network_get(endpoint, params)
        if r.status_code == 429:
            self.last_error = "limite API per minuto raggiunto"
            raise RuntimeError("Limite API per minuto raggiunto; riprova tra circa un minuto")
        if r.status_code in (401, 403):
            self.last_error = "chiave API non valida o accesso negato"
            raise RuntimeError("Chiave API non valida o accesso API-Football negato")
        try:
            r.raise_for_status()
        except requests.HTTPError as exc:
            self.last_error = f"HTTP {r.status_code}"
            raise RuntimeError(f"API-Football HTTP {r.status_code}") from exc

        body = r.json()
        result = APIResult(
            response=body.get("response") or [],
            errors=body.get("errors") or {},
            results=int(body.get("results") or 0),
        )
        if result.errors:
            msg = str(result.errors)
            self.last_error = msg
            raise RuntimeError(f"API-Football: {msg}")
        if ttl:
            self.cache.set(endpoint, params, {
                "response": result.response,
                "errors": result.errors,
                "results": result.results,
            })
        return result

    @staticmethod
    def _best_player_match(query: str, rows: list[dict[str, Any]]):
        target = normalize(query)
        scored = []
        for row in rows:
            p = row.get("player") or row
            candidates = [
                p.get("name", ""),
                f'{p.get("firstname", "")} {p.get("lastname", "")}',
                p.get("lastname", ""),
            ]
            score = max((fuzz.ratio(target, normalize(x)) for x in candidates if x), default=0)
            if target and all(tok in normalize(" ".join(candidates)) for tok in target.split()):
                score = min(100, score + 8)
            scored.append((score, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        return (scored[0][1], int(scored[0][0])) if scored else (None, 0)

    def resolve_player_with_stats(self, query: str, season: int):
        token = query.split()[-1]
        res = self._get("/players", {"search": token, "season": season}, ttl=30 * 86400)
        row, confidence = self._best_player_match(query, res.response)
        return row, confidence

    def search_profile(self, query: str) -> tuple[dict[str, Any] | None, int]:
        token = query.split()[-1]
        result = self._get("/players/profiles", {"search": token}, ttl=30 * 86400)
        row, confidence = self._best_player_match(query, result.response)
        return ((row or {}).get("player") if row else None), confidence

    def player_stats(self, player_id: int, season: int):
        res = self._get("/players", {"id": player_id, "season": season}, ttl=12 * 3600)
        return res.response[0] if res.response else None

    @staticmethod
    def best_stat_block(player_row):
        blocks = (player_row or {}).get("statistics") or []
        if not blocks:
            return None
        return max(blocks, key=lambda b: (
            (b.get("games") or {}).get("appearences") or 0,
            (b.get("games") or {}).get("minutes") or 0,
        ))

    def upcoming_fixtures_by_league(self, league_ids: list[int], season: int, days: int = 14):
        today = datetime.now(timezone.utc).date()
        end = today + timedelta(days=days)
        fixtures = []
        for league_id in sorted({int(x) for x in league_ids if x}):
            res = self._get("/fixtures", {
                "league": league_id,
                "season": season,
                "from": today.isoformat(),
                "to": end.isoformat(),
                "timezone": "Europe/Rome",
            }, ttl=30 * 60)
            fixtures.extend(res.response)
        fixtures.sort(key=lambda x: ((x.get("fixture") or {}).get("timestamp") or 0))
        return fixtures

    @staticmethod
    def map_next_fixture_by_team(fixtures: list[dict[str, Any]], team_ids: list[int]):
        wanted = {int(x) for x in team_ids if x}
        out = {x: None for x in wanted}
        for fx in fixtures:
            teams = fx.get("teams") or {}
            ids = {
                (teams.get("home") or {}).get("id"),
                (teams.get("away") or {}).get("id"),
            }
            for tid in wanted.intersection(ids):
                if out[tid] is None:
                    out[tid] = fx
        return out

    def next_fixture(self, team_id: int):
        res = self._get("/fixtures", {"team": team_id, "next": 1, "timezone": "Europe/Rome"}, ttl=30 * 60)
        return res.response[0] if res.response else None

    def injuries_for_fixtures(self, fixture_ids: list[int]):
        ids = sorted({int(x) for x in fixture_ids if x})
        if not ids:
            return []
        res = self._get("/injuries", {"ids": "-".join(map(str, ids)), "timezone": "Europe/Rome"}, ttl=2 * 3600)
        return res.response

    def prediction(self, fixture_id: int):
        res = self._get("/predictions", {"fixture": fixture_id}, ttl=6 * 3600)
        return res.response[0] if res.response else None

    def fixture_details(self, fixture_id: int):
        res = self._get("/fixtures", {"id": fixture_id, "timezone": "Europe/Rome"}, ttl=5 * 60)
        return res.response[0] if res.response else None

    def recent_team_fixtures(self, team_id: int, season: int, last: int = 5):
        res = self._get("/fixtures", {
            "team": team_id, "season": season, "last": last, "timezone": "Europe/Rome"
        }, ttl=12 * 3600)
        return res.response

    def fixtures_details_batch(self, fixture_ids: list[int]):
        ids = sorted({int(x) for x in fixture_ids if x})
        out = {}
        for start in range(0, len(ids), 20):
            chunk = ids[start:start + 20]
            res = self._get("/fixtures", {
                "ids": "-".join(map(str, chunk)), "timezone": "Europe/Rome"
            }, ttl=12 * 3600)
            for fx in res.response:
                fid = (fx.get("fixture") or {}).get("id")
                if fid:
                    out[fid] = fx
        return out

    @staticmethod
    def recent_player_stats_from_details(details: list[dict[str, Any]], player_id: int):
        rows = []
        for fx in details:
            for team_block in fx.get("players") or []:
                for item in team_block.get("players") or []:
                    if (item.get("player") or {}).get("id") != player_id:
                        continue
                    stats = (item.get("statistics") or [{}])[0]
                    games = stats.get("games") or {}
                    shots = stats.get("shots") or {}
                    goals = stats.get("goals") or {}
                    passes = stats.get("passes") or {}
                    penalty = stats.get("penalty") or {}
                    rows.append({
                        "minutes": games.get("minutes") or 0,
                        "rating": games.get("rating"),
                        "starter": not bool(games.get("substitute")),
                        "shots": shots.get("total") or 0,
                        "shots_on": shots.get("on") or 0,
                        "goals": goals.get("total") or 0,
                        "assists": goals.get("assists") or 0,
                        "key_passes": passes.get("key") or 0,
                        "pen_scored": penalty.get("scored") or 0,
                        "pen_missed": penalty.get("missed") or 0,
                    })
        return rows

    @staticmethod
    def official_lineup_status(fixture: dict[str, Any], player_id: int) -> str:
        for team in fixture.get("lineups") or []:
            for p in team.get("startXI") or []:
                if (p.get("player") or {}).get("id") == player_id:
                    return "starter"
            for p in team.get("substitutes") or []:
                if (p.get("player") or {}).get("id") == player_id:
                    return "bench"
        return "unknown"

    @staticmethod
    def is_close_to_kickoff(fixture: dict[str, Any], hours: float = 3.0) -> bool:
        raw = (fixture.get("fixture") or {}).get("date")
        if not raw:
            return False
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            delta = (dt.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
            return -3600 <= delta <= hours * 3600
        except ValueError:
            return False

    def diagnostics(self):
        return {
            "network_calls": self.calls_this_run,
            "cache_hits": self.cache_hits,
            "daily_limit": self.daily_limit,
            "daily_remaining": self.daily_remaining,
            "minute_limit": self.minute_limit,
            "minute_remaining": self.minute_remaining,
            "wait_seconds": round(self.rate_wait_seconds, 1),
            "last_error": self.last_error,
        }
