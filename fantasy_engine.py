from __future__ import annotations

from dataclasses import dataclass, asdict
from statistics import mean
from typing import Any

FORMATIONS = {
    "3-4-3": {"P": 1, "D": 3, "C": 4, "A": 3},
    "3-5-2": {"P": 1, "D": 3, "C": 5, "A": 2},
    "4-3-3": {"P": 1, "D": 4, "C": 3, "A": 3},
    "4-4-2": {"P": 1, "D": 4, "C": 4, "A": 2},
    "4-5-1": {"P": 1, "D": 4, "C": 5, "A": 1},
    "5-3-2": {"P": 1, "D": 5, "C": 3, "A": 2},
    "5-4-1": {"P": 1, "D": 5, "C": 4, "A": 1},
}


def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


def f(v, default=0.0):
    try:
        return default if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return default


def status_label(score: float) -> str:
    if score >= 88:
        return "🔥 Scelta obbligata"
    if score >= 76:
        return "✅ Consigliato"
    if score >= 64:
        return "👍 Schierabile"
    if score >= 50:
        return "⚠️ Rischioso"
    return "⛔ Da evitare"


def recent_summary(matches: list[dict[str, Any]] | None):
    matches = matches or []
    if not matches:
        return {
            "matches": 0, "rating": None, "minutes": 0, "starts": 0,
            "goals": 0, "assists": 0, "shots_on": 0, "key_passes": 0,
            "threat": 50.0,
        }
    ratings = []
    for x in matches:
        try:
            if x.get("rating") is not None:
                ratings.append(float(x["rating"]))
        except (ValueError, TypeError):
            pass
    shots_on = sum(f(x.get("shots_on")) for x in matches)
    goals = sum(f(x.get("goals")) for x in matches)
    assists = sum(f(x.get("assists")) for x in matches)
    key = sum(f(x.get("key_passes")) for x in matches)
    mins = sum(f(x.get("minutes")) for x in matches)
    starts = sum(1 for x in matches if x.get("starter"))
    n = len(matches)
    # Non è xG: proxy trasparente di pericolosità recente.
    threat = clamp(35 + 7 * (shots_on / n) + 15 * (goals / n) + 9 * (assists / n) + 2 * (key / n))
    return {
        "matches": n,
        "rating": mean(ratings) if ratings else None,
        "minutes": mins,
        "starts": starts,
        "goals": goals,
        "assists": assists,
        "shots_on": shots_on,
        "key_passes": key,
        "threat": threat,
    }


def team_prediction_score(prediction, team_id):
    if not prediction or not team_id:
        return None
    pred = prediction.get("predictions") or {}
    perc = pred.get("percent") or {}
    teams = prediction.get("teams") or {}
    if team_id == (teams.get("home") or {}).get("id"):
        key = "home"
    elif team_id == (teams.get("away") or {}).get("id"):
        key = "away"
    else:
        return None
    try:
        return float(str(perc.get(key, "")).replace("%", ""))
    except ValueError:
        return None


@dataclass
class PlayerEvaluation:
    name: str
    role: str
    api_player_id: int | None
    resolved_name: str
    team: str
    opponent: str
    home_away: str
    kickoff: str
    p_starter: float
    probable_source_pct: float | None
    source_consensus_pct: float | None
    source_count: int
    source_detail: str
    news_alerts: str
    official_status: str
    unavailable: str
    season_rating: float | None
    recent_rating: float | None
    recent_matches: int
    recent_goals: float
    recent_assists: float
    threat_score: float
    fixture_score: float
    set_piece_score: float
    xg90: float | None
    xa90: float | None
    schierabilita: float
    label: str
    confidence: str
    reason: str

    def to_dict(self):
        return asdict(self)


def evaluate_player(
    *,
    name: str,
    role: str,
    api_player_id: int | None,
    resolved_name: str,
    resolve_confidence: int,
    stat_block: dict[str, Any] | None,
    fixture: dict[str, Any] | None,
    injury: dict[str, Any] | None,
    official_status: str,
    prediction: dict[str, Any] | None,
    probable_pct: float | None = None,
    recent_matches: list[dict[str, Any]] | None = None,
    set_piece_role: dict[str, Any] | None = None,
    xg90: float | None = None,
    xa90: float | None = None,
    manual_note: str = "",
    external_source_estimates: list[dict[str, Any]] | None = None,
    news_adjustment: float = 0.0,
    news_notes: list[str] | None = None,
):
    stat_block = stat_block or {}
    games = stat_block.get("games") or {}
    goals = stat_block.get("goals") or {}
    team = stat_block.get("team") or {}

    apps = f(games.get("appearences"))
    starts = f(games.get("lineups"))
    minutes = f(games.get("minutes"))
    season_rating = f(games.get("rating"), 0) or None
    season_goals = f(goals.get("total"))
    season_assists = f(goals.get("assists"))

    if apps:
        start_rate = starts / apps
        minute_rate = clamp((minutes / apps) / 90, 0, 1)
        model_p = 15 + 72 * start_rate + 13 * minute_rate
    else:
        model_p = 50

    # Consenso multi-fonte. Solo Fantacalcio può fornire una % editoriale vera;
    # per Gazzetta/Sky usiamo stime interne derivate dallo stato (XI/panchina/dubbio).
    estimates = [{"source": "Modello statistiche", "estimate": model_p, "weight": 0.24}]
    if probable_pct is not None:
        estimates.append({"source": "Fantacalcio.it", "estimate": probable_pct, "weight": 0.36})
    for item in (external_source_estimates or []):
        val = item.get("estimate")
        if val is None:
            continue
        source = item.get("source", "Fonte")
        weight = 0.22 if source == "Gazzetta" else 0.18 if source == "Sky Sport" else 0.14
        estimates.append({"source": source, "estimate": float(val), "weight": weight})

    weight_sum = sum(x["weight"] for x in estimates)
    p_starter = sum(x["estimate"] * x["weight"] for x in estimates) / weight_sum
    p_starter = clamp(p_starter + float(news_adjustment or 0.0))

    editorial_estimates = [x["estimate"] for x in estimates if x["source"] != "Modello statistiche"]
    source_consensus = (
        sum(editorial_estimates) / len(editorial_estimates)
        if editorial_estimates else None
    )
    source_count = len(editorial_estimates)
    discrepancy = (
        max(editorial_estimates) - min(editorial_estimates)
        if len(editorial_estimates) >= 2 else 0
    )
    source_detail = " | ".join(
        f'{x["source"]}: {x["estimate"]:.0f}'
        for x in estimates if x["source"] != "Modello statistiche"
    )

    recent = recent_summary(recent_matches)
    recent_rating = recent["rating"]
    if recent_rating is not None:
        form_score = clamp(50 + (recent_rating - 6.0) * 34)
    elif season_rating is not None:
        form_score = clamp(50 + (season_rating - 6.0) * 30)
    else:
        form_score = 52

    threat = recent["threat"]
    if not recent["matches"] and apps:
        threat = clamp(38 + ((season_goals + season_assists) / apps) * 130)

    # xG/xA reali, se inseriti dall'utente/fonte esterna, migliorano l'upside.
    advanced = None
    if xg90 is not None or xa90 is not None:
        advanced = clamp(35 + 62 * f(xg90) + 48 * f(xa90))
        threat = 0.55 * threat + 0.45 * advanced

    fixture_score = 56 if fixture else 50
    team_id = team.get("id")
    opponent, home_away, kickoff = "—", "—", "—"
    if fixture:
        fx = fixture.get("fixture") or {}
        kickoff = fx.get("date", "—")
        teams = fixture.get("teams") or {}
        home, away = teams.get("home") or {}, teams.get("away") or {}
        if team_id == home.get("id"):
            opponent, home_away = away.get("name", "—"), "Casa"
        elif team_id == away.get("id"):
            opponent, home_away = home.get("name", "—"), "Trasferta"
        pred = team_prediction_score(prediction, team_id)
        if pred is not None:
            fixture_score = pred

    set_piece_score = 50.0
    sp = set_piece_role or {}
    pen_rank = sp.get("penalty_rank")
    set_rank = sp.get("set_piece_rank")
    if pen_rank == 1:
        set_piece_score += 35
    elif pen_rank == 2:
        set_piece_score += 18
    elif pen_rank == 3:
        set_piece_score += 8
    if set_rank == 1:
        set_piece_score += 12
    elif set_rank == 2:
        set_piece_score += 7
    elif set_rank == 3:
        set_piece_score += 4
    set_piece_score = clamp(set_piece_score)

    unavailable = ""
    if injury:
        unavailable = f'{injury.get("type") or "Indisponibile"}: {injury.get("reason") or ""}'.strip(": ")
        p_starter = 2
    if official_status == "starter":
        p_starter = 100
    elif official_status == "bench":
        p_starter = 5

    role = role.upper()
    # Senza modificatore difesa: peso maggiore all'upside offensivo di C/A.
    if role == "P":
        score = .69*p_starter + .10*form_score + .21*fixture_score
    elif role == "D":
        score = .59*p_starter + .14*form_score + .15*fixture_score + .08*threat + .04*set_piece_score
    elif role == "C":
        score = .48*p_starter + .18*form_score + .10*fixture_score + .17*threat + .07*set_piece_score
    else:
        score = .45*p_starter + .18*form_score + .13*fixture_score + .18*threat + .06*set_piece_score

    if injury:
        score = min(score, 10)
    elif official_status == "bench":
        score = min(score, 40)
    score = clamp(score)

    reasons = []
    if official_status == "starter":
        reasons.append("titolare ufficiale")
    elif official_status == "bench":
        reasons.append("panchina ufficiale")
    elif probable_pct is not None:
        reasons.append(f"probabile formazione {probable_pct:.0f}%")
    elif p_starter >= 78:
        reasons.append("alta continuità da titolare")
    elif p_starter >= 58:
        reasons.append("titolarità discreta")
    else:
        reasons.append("titolarità incerta")

    if source_count >= 2:
        reasons.append(f"consenso di {source_count} fonti")
    if discrepancy >= 30:
        reasons.append("⚠️ fonti molto discordanti")
    if news_adjustment <= -3:
        reasons.append("notizie recenti negative")
    elif news_adjustment >= 3:
        reasons.append("notizie recenti positive")

    if unavailable:
        reasons.append(unavailable)
    else:
        if recent_rating is not None:
            reasons.append(f"rating ultime {recent['matches']}: {recent_rating:.2f}")
        if recent["goals"] or recent["assists"]:
            reasons.append(f"ultime {recent['matches']}: {recent['goals']:.0f} gol, {recent['assists']:.0f} assist")
        if fixture_score >= 65:
            reasons.append("matchup favorevole")
        elif fixture_score <= 35:
            reasons.append("matchup difficile")
        if pen_rank == 1:
            reasons.append("1° rigorista")
        elif pen_rank == 2:
            reasons.append("2° rigorista")
        if set_rank == 1:
            reasons.append("1° sui piazzati")
        if xg90 is not None or xa90 is not None:
            reasons.append("xG/xA esterni inclusi")
        elif recent["matches"] and threat >= 65:
            reasons.append("pericolosità recente alta")

    if manual_note:
        reasons.append(manual_note.strip())
    # Mostra l'avviso di abbinamento solo se l'API ha effettivamente trovato
    # un giocatore ma con somiglianza bassa. Se l'API e' fallita/non ha risposto,
    # non confondiamo un errore tecnico con un possibile omonimo.
    if api_player_id is not None and resolve_confidence < 75:
        reasons.append("verificare abbinamento API")

    if official_status != "unknown":
        confidence = "Molto alta"
    elif source_count >= 2 and discrepancy < 25 and resolve_confidence >= 75:
        confidence = "Alta"
    elif source_count >= 1 or apps >= 3:
        confidence = "Media"
    else:
        confidence = "Bassa"
    if discrepancy >= 40 and confidence == "Alta":
        confidence = "Media"

    return PlayerEvaluation(
        name=name,
        role=role,
        api_player_id=api_player_id,
        resolved_name=resolved_name,
        team=team.get("name", "—"),
        opponent=opponent,
        home_away=home_away,
        kickoff=kickoff,
        p_starter=round(clamp(p_starter), 1),
        probable_source_pct=probable_pct,
        source_consensus_pct=round(source_consensus, 1) if source_consensus is not None else None,
        source_count=source_count,
        source_detail=source_detail,
        news_alerts=" | ".join((news_notes or [])[:3]),
        official_status=official_status,
        unavailable=unavailable,
        season_rating=round(season_rating, 2) if season_rating is not None else None,
        recent_rating=round(recent_rating, 2) if recent_rating is not None else None,
        recent_matches=recent["matches"],
        recent_goals=recent["goals"],
        recent_assists=recent["assists"],
        threat_score=round(threat, 1),
        fixture_score=round(fixture_score, 1),
        set_piece_score=round(set_piece_score, 1),
        xg90=xg90,
        xa90=xa90,
        schierabilita=round(score, 1),
        label=status_label(score),
        confidence=confidence,
        reason="; ".join(reasons),
    )


def best_lineup(players: list[PlayerEvaluation]):
    best = None
    for formation, needs in FORMATIONS.items():
        chosen = []
        for role, n in needs.items():
            pool = sorted(
                [p for p in players if p.role == role],
                key=lambda x: (x.schierabilita, x.p_starter),
                reverse=True,
            )
            if len(pool) < n:
                chosen = []
                break
            chosen += pool[:n]
        if not chosen:
            continue
        total = sum(p.schierabilita for p in chosen)
        if best is None or total > best["total"]:
            best = {"formation": formation, "players": chosen, "total": total}
    return best


def bench(players: list[PlayerEvaluation], starters: list[PlayerEvaluation]):
    used = {(x.name, x.role) for x in starters}
    return sorted(
        [x for x in players if (x.name, x.role) not in used],
        key=lambda x: (x.p_starter, x.schierabilita),
        reverse=True,
    )
