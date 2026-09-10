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
    """API-Football client ottimizzato per il piano Free."""

    def __init__(self, api_key: str, cache_dir: str = ".fantacache"):
        self.api_key = api_key.strip()
        self.session = requests.Session()
        self.session.headers.update({"x-apisports-key": self.api_key})
        self.cache = DiskCache(cache_dir)
        self.calls_this_run = 0
        self.cache_hits = 0
        self.error_count = 0
        self.rate_limit_hits = 0
        self.waited_seconds = 0.0
        self.last_errors: list[str] = []
        self.daily_remaining: str | None = None
        self.daily_limit: str | None = None
        self.minute_remaining: str | None = None
        self.minute_limit: str | None = None
        self._last_network_call = 0.0

    @staticmethod
    def _as_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _remember_error(self, message: str):
        self.error_count += 1
        if message not in self.last_errors:
            self.last_errors.append(message[:220])
        self.last_errors = self.last_errors[-5:]

    def _pace(self):
        limit = self._as_int(self.minute_limit)
        min_interval = 6.7 if limit is not None and limit <= 10 else 0.0
        if not min_interval:
            return
        elapsed = time.monotonic() - self._last_network_call
        wait = min_interval - elapsed
        if wait > 0:
            time.sleep(wait)
            self.waited_seconds += wait

    def _read_headers(self, r: requests.Response):
        self.daily_remaining = r.headers.get("x-ratelimit-requests-remaining", self.daily_remaining)
        self.daily_limit = r.headers.get("x-ratelimit-requests-limit", self.daily_limit)
        self.minute_remaining = r.headers.get("X-RateLimit-Remaining", self.minute_remaining)
        self.minute_limit = r.headers.get("X-RateLimit-Limit", self.minute_limit)

    def _get(self, endpoint: str, params: dict[str, Any], ttl: int = 0) -> APIResult:
        endpoint = endpoint if endpoint.startswith("/") else "/" + endpoint
        if ttl:
            cached = self.cache.get(endpoint, params, ttl)
            if cached is not None:
                self.cache_hits += 1
                return APIResult(**cached)

        last_exc = None
        for attempt in range(3):
            self._pace()
            try:
                r = self.session.get(BASE_URL + endpoint, params=params, timeout=25)
                self._last_network_call = time.monotonic()
                self.calls_this_run += 1
                self._read_headers(r)

                if r.status_code == 429:
                    self.rate_limit_hits += 1
                    self._remember_error("rate limit API-Football raggiunto")
                    if attempt < 2:
                        wait = 15 * (attempt + 1)
                        time.sleep(wait)
                        self.waited_seconds += wait
                        continue

                r.raise_for_status()
                body = r.json()
                result = APIResult(
                    response=body.get("response") or [],
                    errors=body.get("errors") or {},
                    results=int(body.get("results") or 0),
                )
                if result.errors:
                    message = f"API-Football: {result.errors}"
                    self._remember_error(message)
                    raise RuntimeError(message)
                if ttl:
                    self.cache.set(endpoint, params, {
                        "response": result.response,
                        "errors": result.errors,
                        "results": result.results,
                    })
                return result
            except requests.HTTPError as exc:
                last_exc = exc
                if getattr(exc.response, "status_code", None) != 429:
                    self._remember_error(f"HTTP {getattr(exc.response, 'status_code', '?')} su {endpoint}")
                    break
            except requests.RequestException as exc:
                last_exc = exc
                self._remember_error(f"rete/API: {type(exc).__name__}")
                break

        if last_exc:
            raise RuntimeError(str(last_exc)) from last_exc
        raise RuntimeError("Errore API-Football non specificato")

    @staticmethod
    def _name_score(target: str, player: dict[str, Any]) -> float:
        target_n = normalize(target)
        candidates = [
            player.get("name", ""),
            f'{player.get("firstname", "")} {player.get("lastname", "")}',
            player.get("lastname", ""),
        ]
        best = 0.0
        for raw in candidates:
            cand = normalize(raw)
            if not cand:
                continue
            score = max(
                fuzz.ratio(target_n, cand),
                fuzz.partial_ratio(target_n, cand),
                fuzz.token_set_ratio(target_n, cand),
            )
            if target_n == cand:
                score = 100
            elif len(target_n) >= 5 and target_n in cand:
                score = max(score, 96)
            best = max(best, score)
        return best

    @staticmethod
    def _team_match_score(row: dict[str, Any], team_hint: str) -> float:
        hint = normalize(team_hint)
        if not hint:
            return 0.0
        best = 0.0
        for block in row.get("statistics") or []:
            team = normalize(((block.get("team") or {}).get("name")) or "")
            if team:
                best = max(best, fuzz.ratio(hint, team), fuzz.token_set_ratio(hint, team))
        return best

    def _id_map_path(self) -> Path:
        return self.cache.folder / "player_ids.json"

    def cached_player_id(self, query: str, max_age_days: int = 180):
        p = self._id_map_path()
        if not p.exists():
            return None
        try:
            if time.time() - p.stat().st_mtime > max_age_days * 86400:
                return None
            data = json.loads(p.read_text(encoding="utf-8"))
            return data.get(normalize(query))
        except Exception:
            return None

    def remember_player_id(self, query: str, row: dict[str, Any], confidence: int):
        player = row.get("player") or {}
        pid = player.get("id")
        if not pid or confidence < 75:
            return
        p = self._id_map_path()
        try:
            data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except Exception:
            data = {}
        data[normalize(query)] = {
            "id": int(pid),
            "name": player.get("name") or query,
            "confidence": int(confidence),
            "saved_at": time.time(),
        }
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def search_player_stats(self, query: str, season: int, team_hint: str = ""):
        """Trova giocatore + statistiche con una sola chiamata."""
        token = query.split()[-1].replace("'", "")
        result = self._get("/players", {"search": token, "season": season}, ttl=12 * 3600)
        if not result.response:
            return None, 0

        scored = []
        for row in result.response:
            player = row.get("player") or {}
            name_score = self._name_score(query, player)
            team_score = self._team_match_score(row, team_hint)
            apps = max(
                [((b.get("games") or {}).get("appearences") or 0) for b in row.get("statistics") or []]
                or [0]
            )
            combined = name_score + (18 if team_score >= 82 else 0) + min(4, apps / 5)
            scored.append((combined, name_score, team_score, apps, row))

        scored.sort(key=lambda x: (x[0], x[3]), reverse=True)
        _, name_score, team_score, _, row = scored[0]
        confidence = int(min(100, name_score + (5 if team_score >= 82 else 0)))
        self.remember_player_id(query, row, confidence)
        return row, confidence

    def player_stats(self, player_id: int, season: int):
        res = self._get("/players", {"id": player_id, "season": season}, ttl=12 * 3600)
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

    def next_fixtures_for_league(self, league_id: int, season: int, count: int = 30):
        res = self._get(
            "/fixtures",
            {"league": league_id, "season": season, "next": count, "timezone": "Europe/Rome"},
            ttl=60 * 60,
        )
        return res.response

    def next_fixture(self, team_id: int):
        res = self._get(
            "/fixtures",
            {"team": team_id, "next": 1, "timezone": "Europe/Rome"},
            ttl=60 * 60,
        )
        return res.response[0] if res.response else None

    def injuries_for_fixtures(self, fixture_ids: list[int]):
        ids = sorted({int(x) for x in fixture_ids if x})
        if not ids:
            return []
        res = self._get(
            "/injuries",
            {"ids": "-".join(map(str, ids)), "timezone": "Europe/Rome"},
            ttl=3 * 3600,
        )
        return res.response

    def prediction(self, fixture_id: int):
        res = self._get("/predictions", {"fixture": fixture_id}, ttl=3 * 3600)
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
            {"team": team_id, "season": season, "last": last, "timezone": "Europe/Rome"},
            ttl=12 * 3600,
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
                ttl=12 * 3600,
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

    def diagnostics(self) -> dict[str, Any]:
        return {
            "network_calls": self.calls_this_run,
            "cache_hits": self.cache_hits,
            "daily_remaining": self.daily_remaining,
            "daily_limit": self.daily_limit,
            "minute_remaining": self.minute_remaining,
            "minute_limit": self.minute_limit,
            "rate_limit_hits": self.rate_limit_hits,
            "waited_seconds": round(self.waited_seconds, 1),
            "errors": self.error_count,
            "last_errors": self.last_errors,
        }
