from __future__ import annotations

import hashlib
import json
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from rapidfuzz import fuzz

BASE_URL = "https://v3.football.api-sports.io"


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.upper().replace("-", " ").replace(".", " ")
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
    def __init__(self, api_key: str, cache_dir: str = ".fantacache"):
        self.api_key = api_key.strip()
        self.session = requests.Session()
        self.session.headers.update({"x-apisports-key": self.api_key})
        self.cache = DiskCache(cache_dir)
        self.calls_this_run = 0
        self.daily_remaining: str | None = None

    def _get(self, endpoint: str, params: dict[str, Any], ttl: int = 0) -> APIResult:
        endpoint = endpoint if endpoint.startswith("/") else "/" + endpoint
        if ttl:
            cached = self.cache.get(endpoint, params, ttl)
            if cached is not None:
                return APIResult(**cached)

        r = self.session.get(BASE_URL + endpoint, params=params, timeout=20)
        self.calls_this_run += 1
        self.daily_remaining = r.headers.get("x-ratelimit-requests-remaining", self.daily_remaining)
        r.raise_for_status()
        body = r.json()
        result = APIResult(
            response=body.get("response") or [],
            errors=body.get("errors") or {},
            results=int(body.get("results") or 0),
        )
        if result.errors:
            raise RuntimeError(f"API-Football: {result.errors}")
        if ttl:
            self.cache.set(
                endpoint,
                params,
                {"response": result.response, "errors": result.errors, "results": result.results},
            )
        return result

    def search_profile(self, query: str) -> tuple[dict[str, Any] | None, int]:
        token = query.split()[-1]
        result = self._get("/players/profiles", {"search": token}, ttl=30 * 86400)
        if not result.response:
            return None, 0
        target = normalize(query)
        scored = []
        for row in result.response:
            p = row.get("player") or {}
            candidates = [
                p.get("name", ""),
                f'{p.get("firstname","")} {p.get("lastname","")}',
                p.get("lastname", ""),
            ]
            score = max((fuzz.ratio(target, normalize(x)) for x in candidates if x), default=0)
            scored.append((score, p))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1], int(scored[0][0])

    def player_stats(self, player_id: int, season: int):
        res = self._get("/players", {"id": player_id, "season": season}, ttl=4 * 3600)
        return res.response[0] if res.response else None

    @staticmethod
    def best_stat_block(player_row):
        blocks = (player_row or {}).get("statistics") or []
        if not blocks:
            return None
        return max(
            blocks,
            key=lambda b: (
                (b.get("games") or {}).get("appearences") or 0,
                (b.get("games") or {}).get("minutes") or 0,
            ),
        )

    def next_fixture(self, team_id: int):
        res = self._get(
            "/fixtures",
            {"team": team_id, "next": 1, "timezone": "Europe/Rome"},
            ttl=30 * 60,
        )
        return res.response[0] if res.response else None

    def injuries_for_fixtures(self, fixture_ids: list[int]):
        ids = sorted({int(x) for x in fixture_ids if x})
        if not ids:
            return []
        res = self._get(
            "/injuries",
            {"ids": "-".join(map(str, ids)), "timezone": "Europe/Rome"},
            ttl=2 * 3600,
        )
        return res.response

    def prediction(self, fixture_id: int):
        res = self._get("/predictions", {"fixture": fixture_id}, ttl=60 * 60)
        return res.response[0] if res.response else None

    def fixture_details(self, fixture_id: int):
        res = self._get(
            "/fixtures",
            {"id": fixture_id, "timezone": "Europe/Rome"},
            ttl=5 * 60,
        )
        return res.response[0] if res.response else None

    def recent_team_fixtures(self, team_id: int, season: int, last: int = 5):
        res = self._get(
            "/fixtures",
            {
                "team": team_id,
                "season": season,
                "last": last,
                "timezone": "Europe/Rome",
            },
            ttl=6 * 3600,
        )
        return res.response

    def fixtures_details_batch(self, fixture_ids: list[int]):
        ids = sorted({int(x) for x in fixture_ids if x})
        out = {}
        for start in range(0, len(ids), 20):
            chunk = ids[start:start + 20]
            res = self._get(
                "/fixtures",
                {"ids": "-".join(map(str, chunk)), "timezone": "Europe/Rome"},
                ttl=6 * 3600,
            )
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
                    rows.append(
                        {
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
                        }
                    )
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
