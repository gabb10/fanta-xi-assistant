from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import quote_plus, urljoin

import feedparser
import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

GAZZETTA_PROB_URL = "https://www.gazzetta.it/Calcio/prob_form/"
SKY_TEAM_URL = "https://sport.sky.it/calcio/serie-a/probabili-formazioni/{slug}"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=it&gl=IT&ceid=IT:it"

SKY_SLUGS = {
    "ATALANTA": "atalanta", "BOLOGNA": "bologna", "CAGLIARI": "cagliari",
    "COMO": "como", "CREMONESE": "cremonese", "FIORENTINA": "fiorentina",
    "FROSINONE": "frosinone", "GENOA": "genoa", "INTER": "inter",
    "JUVENTUS": "juventus", "LAZIO": "lazio", "LECCE": "lecce",
    "MILAN": "milan", "MONZA": "monza", "NAPOLI": "napoli",
    "PARMA": "parma", "PISA": "pisa", "ROMA": "roma",
    "SASSUOLO": "sassuolo", "TORINO": "torino", "UDINESE": "udinese",
    "VENEZIA": "venezia", "VERONA": "verona",
}

NEWS_DOMAINS = [
    "sport.sky.it", "gazzetta.it", "fantacalcio.it",
    "corrieredellosport.it", "tuttomercatoweb.com",
]

NEGATIVE = [
    "infortun", "lesione", "out", "forfait", "salta", "squalificat",
    "indisponibil", "operat", "stop", "problema muscolare", "lavoro a parte",
    "si ferma", "non convocat", "ko ",
]
POSITIVE = [
    "recupera", "recuperato", "convocato", "convocazione", "titolare",
    "dal 1'", "dal primo minuto", "rientra", "rientro", "disponibile",
    "in gruppo", "si allena con il gruppo",
]


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.upper().replace("-", " ").replace(".", " ").replace("'", " ")
    return " ".join(s.split())


def _name_score(target: str, candidate: str) -> float:
    a, b = norm(target), norm(candidate)
    if not a or not b:
        return 0
    return max(fuzz.ratio(a, b), fuzz.partial_ratio(a, b))


def _contains_name(text: str, name: str) -> bool:
    nt, nn = norm(text), norm(name)
    if nn in nt:
        return True
    parts = nn.split()
    if len(parts) >= 2 and len(parts[-1]) >= 5:
        return parts[-1] in nt
    return False


class TinyDiskCache:
    def __init__(self, folder: str):
        self.root = Path(folder)
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        import hashlib
        return self.root / (hashlib.sha1(key.encode()).hexdigest() + ".txt")

    def get(self, key: str, ttl: int):
        p = self._p(key)
        if not p.exists() or time.time() - p.stat().st_mtime > ttl:
            return None
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            return None

    def set(self, key: str, value: str):
        self._p(key).write_text(value, encoding="utf-8")


@dataclass
class SourceSignal:
    source: str
    estimate: float | None
    state: str
    note: str
    url: str
    freshness: str = ""
    exact_percentage: bool = False

    def to_dict(self):
        return asdict(self)


@dataclass
class NewsItem:
    player: str
    source: str
    title: str
    url: str
    published: str
    polarity: int
    age_hours: float | None

    def to_dict(self):
        return asdict(self)


class MultiSource:
    def __init__(self, cache_dir: str = ".sourcecache"):
        self.cache = TinyDiskCache(cache_dir)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; FantaXIAssistant/3.0; personal-use)"
        })

    def _get(self, url: str, ttl: int = 900) -> str:
        cached = self.cache.get(url, ttl)
        if cached is not None:
            return cached
        r = self.session.get(url, timeout=15)
        r.raise_for_status()
        text = r.text
        self.cache.set(url, text)
        return text

    def gazzetta_match_links(self) -> list[str]:
        try:
            html = self._get(GAZZETTA_PROB_URL, ttl=900)
            soup = BeautifulSoup(html, "html.parser")
            links = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "prob_form" in href and "match=" in href:
                    links.append(urljoin(GAZZETTA_PROB_URL, href))
            return list(dict.fromkeys(links))
        except Exception:
            return []

    def _gazzetta_signal_from_page(self, html: str, player: str, url: str) -> SourceSignal | None:
        soup = BeautifulSoup(html, "html.parser")
        lines = [x.strip() for x in soup.get_text("\n").splitlines() if x.strip()]
        full = "\n".join(lines)
        if not _contains_name(full, player):
            return None

        for label in ["Indisponibili:", "Squalificati:"]:
            for line in lines:
                if label.lower() in line.lower() and _contains_name(line, player):
                    return SourceSignal("Gazzetta", 2.0, "out", line[:220], url)

        for line in lines:
            if "ballottaggio" in line.lower() and _contains_name(line, player):
                m = re.search(r"(\d{1,3})\s*[-–]\s*(\d{1,3})\s*%", line)
                if m:
                    names_part = line.split(":", 1)[-1]
                    names = re.split(r"[-–]", re.sub(r"\d+\s*%", "", names_part))
                    names = [x.strip(" ,") for x in names if x.strip(" ,")]
                    pcts = [float(m.group(1)), float(m.group(2))]
                    if len(names) >= 2:
                        scores = [_name_score(player, x) for x in names[:2]]
                        idx = 0 if scores[0] >= scores[1] else 1
                        return SourceSignal("Gazzetta", pcts[idx], "ballottaggio", line[:220], url, exact_percentage=True)
                return SourceSignal("Gazzetta", 50.0, "ballottaggio", line[:220], url)

        disposition = next((i for i, x in enumerate(lines) if "DISPOSIZIONE IN CAMPO" in x.upper()), None)
        if disposition is not None and _contains_name("\n".join(lines[:disposition]), player):
            return SourceSignal("Gazzetta", 84.0, "starter_probabile", "Presente nell'XI probabile Gazzetta", url)

        for line in lines:
            if line.lower().startswith("panchina:") and _contains_name(line, player):
                return SourceSignal("Gazzetta", 20.0, "panchina_probabile", line[:220], url)
        return None

    def gazzetta_signals(self, players: list[dict[str, str]]) -> dict[str, SourceSignal]:
        out = {}
        links = self.gazzetta_match_links()
        if not links:
            return out
        unresolved = {norm(x["name"]): x for x in players}
        for url in links[:14]:
            try:
                html = self._get(url, ttl=900)
            except Exception:
                continue
            page_norm = norm(BeautifulSoup(html, "html.parser").get_text(" "))
            for key, p in list(unresolved.items()):
                team = p.get("team", "")
                if team and norm(team) not in page_norm and norm(p["name"]) not in page_norm:
                    continue
                sig = self._gazzetta_signal_from_page(html, p["name"], url)
                if sig:
                    out[key] = sig
                    unresolved.pop(key, None)
            if not unresolved:
                break
        return out

    def _sky_team_slug(self, team: str) -> str | None:
        n = norm(team)
        if n in SKY_SLUGS:
            return SKY_SLUGS[n]
        if n and len(n.split()) <= 2:
            return n.lower().replace(" ", "-")
        return None

    def sky_team_page(self, team: str):
        slug = self._sky_team_slug(team)
        if not slug:
            return None, None
        url = SKY_TEAM_URL.format(slug=slug)
        try:
            return self._get(url, ttl=900), url
        except Exception:
            return None, None

    def _sky_signal_from_page(self, html: str, player: str, url: str) -> SourceSignal | None:
        soup = BeautifulSoup(html, "html.parser")
        lines = [x.strip() for x in soup.get_text("\n").splitlines() if x.strip()]
        full = "\n".join(lines)
        if not _contains_name(full, player):
            return None

        def section_index(label):
            return next((i for i, x in enumerate(lines) if norm(x) == norm(label)), None)

        idx_res = section_index("Riserve")
        idx_doubt = section_index("In dubbio")
        idx_susp = section_index("Squalificati")
        idx_out = section_index("Indisponibili")

        for label, idx in [("Indisponibili", idx_out), ("Squalificati", idx_susp)]:
            if idx is None:
                continue
            ends = [x for x in [idx_res, idx_doubt, idx_susp, idx_out] if x is not None and x > idx]
            end = min(ends) if ends else min(len(lines), idx + 30)
            if _contains_name("\n".join(lines[idx+1:end]), player):
                return SourceSignal("Sky Sport", 2.0, "out", f"{label} su Sky Sport", url)

        if idx_doubt is not None:
            ends = [x for x in [idx_susp, idx_out] if x is not None and x > idx_doubt]
            end = min(ends) if ends else min(len(lines), idx_doubt + 30)
            if _contains_name("\n".join(lines[idx_doubt+1:end]), player):
                return SourceSignal("Sky Sport", 48.0, "dubbio", "Segnalato in dubbio da Sky Sport", url)

        if idx_res is not None and _contains_name("\n".join(lines[:idx_res]), player):
            return SourceSignal("Sky Sport", 84.0, "starter_probabile", "Presente nell'XI probabile Sky Sport", url)

        if idx_res is not None:
            ends = [x for x in [idx_doubt, idx_susp, idx_out] if x is not None and x > idx_res]
            end = min(ends) if ends else min(len(lines), idx_res + 45)
            if _contains_name("\n".join(lines[idx_res+1:end]), player):
                return SourceSignal("Sky Sport", 20.0, "panchina_probabile", "Presente tra le riserve nella probabile Sky Sport", url)
        return None

    def sky_signals(self, players: list[dict[str, str]]) -> dict[str, SourceSignal]:
        out = {}
        teams = {}
        for p in players:
            if p.get("team"):
                teams.setdefault(p["team"], []).append(p)
        for team, plist in teams.items():
            html, url = self.sky_team_page(team)
            if not html:
                continue
            for p in plist:
                sig = self._sky_signal_from_page(html, p["name"], url)
                if sig:
                    out[norm(p["name"])] = sig
        return out

    @staticmethod
    def _polarity(text: str) -> int:
        t = text.lower()
        neg = sum(1 for x in NEGATIVE if x in t)
        pos = sum(1 for x in POSITIVE if x in t)
        if neg > pos:
            return -1
        if pos > neg:
            return 1
        return 0

    @staticmethod
    def _age_hours(entry):
        parsed = getattr(entry, "published_parsed", None)
        published = getattr(entry, "published", "") or ""
        if not parsed:
            return published, None
        import calendar
        ts = calendar.timegm(parsed)
        age = max(0.0, (time.time() - ts) / 3600)
        return published, age

    def news_for_players(self, names: list[str], max_age_hours: int = 96) -> dict[str, list[NewsItem]]:
        out = {norm(x): [] for x in names}
        batch_size = 5
        for start in range(0, len(names), batch_size):
            batch = names[start:start+batch_size]
            names_q = " OR ".join(f'"{x}"' for x in batch)
            domains_q = " OR ".join(f"site:{x}" for x in NEWS_DOMAINS)
            query = quote_plus(f"({names_q}) ({domains_q}) when:4d")
            url = GOOGLE_NEWS_RSS.format(query=query)
            try:
                xml = self._get(url, ttl=1800)
                feed = feedparser.loads(xml)
            except Exception:
                continue

            for e in feed.entries[:60]:
                title = getattr(e, "title", "") or ""
                desc = getattr(e, "summary", "") or ""
                blob = title + " " + BeautifulSoup(desc, "html.parser").get_text(" ")
                published, age = self._age_hours(e)
                if age is not None and age > max_age_hours:
                    continue
                try:
                    src = getattr(e, "source", {}).get("title", "")
                except Exception:
                    src = ""
                link = getattr(e, "link", "") or ""
                pol = self._polarity(blob)
                for name in batch:
                    if _contains_name(blob, name):
                        out[norm(name)].append(NewsItem(
                            player=name, source=src or "Google News", title=title[:240],
                            url=link, published=published, polarity=pol, age_hours=age
                        ))

        for key, items in out.items():
            seen, unique = set(), []
            for x in sorted(items, key=lambda z: z.age_hours if z.age_hours is not None else 9999):
                sig = norm(x.title)
                if sig in seen:
                    continue
                seen.add(sig)
                unique.append(x)
            out[key] = unique[:5]
        return out

    @staticmethod
    def news_adjustment(items: list[NewsItem]):
        if not items:
            return 0.0, []
        score, notes = 0.0, []
        for item in items[:4]:
            age = item.age_hours if item.age_hours is not None else 72
            freshness = max(0.25, 1.0 - age / 120.0)
            score += item.polarity * 3.0 * freshness
            if item.polarity != 0:
                notes.append(item.title)
        return max(-9.0, min(9.0, score)), notes[:3]
