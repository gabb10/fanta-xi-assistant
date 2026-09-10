from __future__ import annotations

import copy
import html
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from api_football import APIFootball
from competition_context import (
    cup_adjustment,
    cup_window,
    find_serie_a_fixture,
    format_kickoff,
    next_serie_a_fixtures,
    opponent_from_fixture,
    team_id_from_fixture,
)
from fantacalcio_sources import (
    PROBABILI_URL,
    RIGORISTI_URL,
    ROSTER_TEAM_HINTS,
    fetch_probable_percentages,
    fetch_set_piece_roles,
    norm,
)
from fantasy_engine import best_lineup, bench, clamp, evaluate_player, status_label
from multi_source import GAZZETTA_PROB_URL, MultiSource

BASE = Path(__file__).resolve().parent
ROSTER = BASE / "roster.csv"
OVERRIDES = BASE / "manual_overrides.csv"

st.set_page_config(page_title="Fanta XI V3.3", page_icon="⚽", layout="wide")

st.markdown("""
<style>
.block-container{padding-top:.9rem;padding-bottom:3rem;max-width:1180px}
h1{font-size:clamp(1.7rem,7vw,2.55rem)!important}
div[data-testid="stMetric"]{border:1px solid rgba(148,163,184,.2);border-radius:16px;padding:10px}
.player-card{border:1px solid rgba(148,163,184,.22);border-radius:18px;padding:14px;margin:10px 0;background:rgba(15,23,42,.55)}
.player-title{font-size:1.08rem;font-weight:800}
.player-meta{opacity:.9;font-size:.92rem;margin-top:5px}
.player-date{opacity:.82;font-size:.86rem;margin-top:4px}
.player-reason{margin-top:8px;line-height:1.35}
.badge{display:inline-block;padding:3px 8px;border-radius:99px;border:1px solid rgba(148,163,184,.3);font-size:.77rem;margin:5px 5px 0 0}
.source{opacity:.88;font-size:.82rem;margin-top:7px}
.cup{font-size:.84rem;margin-top:7px}
@media(max-width:640px){.block-container{padding-left:.75rem;padding-right:.75rem}.player-card{padding:12px}button{min-height:44px}}
</style>
""", unsafe_allow_html=True)


def secret_key():
    try:
        return st.secrets.get("API_FOOTBALL_KEY", "") or os.getenv("API_FOOTBALL_KEY", "")
    except Exception:
        return os.getenv("API_FOOTBALL_KEY", "")


def maybe_float(value):
    try:
        if pd.isna(value) or value == "":
            return None
        return float(value)
    except Exception:
        return None


def esc(value):
    return html.escape(str(value or ""))


def choose_stat_block(row, team_hint: str):
    blocks = (row or {}).get("statistics") or []
    if not blocks:
        return None
    hint = norm(team_hint)
    if hint:
        same_team = [b for b in blocks if norm(((b.get("team") or {}).get("name")) or "") == hint]
        if same_team:
            return max(
                same_team,
                key=lambda b: (
                    (b.get("games") or {}).get("appearences") or 0,
                    (b.get("games") or {}).get("minutes") or 0,
                ),
            )
    serie_a = [
        b for b in blocks
        if (b.get("league") or {}).get("id") == 135
        or norm((b.get("league") or {}).get("name", "")) == "SERIE A"
    ]
    if serie_a:
        return max(
            serie_a,
            key=lambda b: (
                (b.get("games") or {}).get("appearences") or 0,
                (b.get("games") or {}).get("minutes") or 0,
            ),
        )
    return max(
        blocks,
        key=lambda b: (
            (b.get("games") or {}).get("appearences") or 0,
            (b.get("games") or {}).get("minutes") or 0,
        ),
    )


st.title("⚽ Fanta XI Assistant V3.3")
st.caption("Serie A + probabilità voto + coppe/turnover + consenso multi-fonte.")

with st.expander("⚙️ Impostazioni", expanded=False):
    api_key = st.text_input("API-Football key", value=secret_key(), type="password")
    c1, c2 = st.columns(2)
    use_predictions = c1.toggle(
        "Matchup API",
        value=False,
        help="Usa una prediction API per la partita. Consuma più quota.",
    )
    official_check = c2.toggle("Lineup ufficiali entro 3 ore", value=True)

    st.markdown("**Fonti probabili / news**")
    s1, s2, s3 = st.columns(3)
    use_fantacalcio = s1.toggle("Fantacalcio.it", value=True)
    use_gazzetta = s2.toggle("Gazzetta", value=True)
    use_sky = s3.toggle("Sky Sport", value=True)
    use_news = st.toggle("Notizie ultime 4 giorni", value=True)
    use_set_pieces = st.toggle("Rigoristi/piazzati Fantacalcio.it", value=True)
    use_cups = st.toggle(
        "Analisi coppe e turnover",
        value=True,
        help="Controlla gli impegni entro 5 giorni dalla prossima Serie A. Se una coppa è già stata giocata, prova anche a leggere i minuti del giocatore.",
    )
    advanced_recent = st.toggle(
        "Ultime 5 avanzate",
        value=False,
        help="Più precisa ma usa più richieste API. Lasciala spenta sul piano Free salvo necessità.",
    )
    clear_cache = st.button("Svuota cache API-Football")
    st.caption("Le fixture di Serie A vengono recuperate con una chiamata unica; i calendari coppe sono memorizzati in cache.")

roster = pd.read_csv(ROSTER, dtype={"api_player_id": "Int64"})
overrides = pd.read_csv(OVERRIDES)

tab_xi, tab_sources, tab_all, tab_data, tab_info = st.tabs(
    ["⭐ XI", "📰 Fonti", "📊 Rosa", "✏️ Dati", "ℹ️ Metodo"]
)

with tab_data:
    st.subheader("Rosa")
    roster_edit = st.data_editor(roster, use_container_width=True, hide_index=True)
    if st.button("💾 Salva rosa"):
        roster_edit.to_csv(ROSTER, index=False)
        st.success("Rosa salvata per questa istanza dell'app.")
    st.subheader("Override opzionali")
    over_edit = st.data_editor(overrides, use_container_width=True, hide_index=True)
    if st.button("💾 Salva override"):
        over_edit.to_csv(OVERRIDES, index=False)
        st.success("Override salvati per questa istanza dell'app.")

with tab_info:
    st.markdown("""
### Come sceglie l'XI
La priorità non è soltanto la titolarità. Il motore separa **% titolare** e **% voto**:
un giocatore può partire in panchina ma avere buone probabilità di entrare e prendere voto.

Senza modificatore difesa il motore privilegia i moduli più offensivi, con **3-4-3 come riferimento**.
Può però passare al 3-5-2 o ad altri moduli quando il terzo attaccante ha un rischio di SV troppo alto.

### Coppe e turnover
Per ogni squadra viene cercata la prossima gara di Serie A e vengono controllate le altre
competizioni nei 5 giorni prima/dopo: Champions League, Europa League, Conference League,
Coppa Italia, Supercoppa e qualsiasi altra competizione non classificata come Serie A o amichevole.

Se una coppa è già stata giocata e i dati sono disponibili, i minuti giocati incidono sul rischio:
90 minuti a pochi giorni dal campionato aumentano il rischio turnover; essere rimasto a riposo può
invece essere un piccolo segnale positivo. Una formazione ufficiale prevale sempre.
""")
    st.link_button("Fantacalcio.it probabili", PROBABILI_URL)
    st.link_button("Gazzetta probabili", GAZZETTA_PROB_URL)
    st.link_button("Fantacalcio.it rigoristi", RIGORISTI_URL)

if clear_cache and api_key:
    APIFootball(api_key, str(BASE / ".fantacache")).cache.clear()
    st.toast("Cache API svuotata")

for key, default in [
    ("evaluations_v33", None),
    ("v33_sources", {}),
    ("v33_news", {}),
    ("v33_cups", {}),
]:
    if key not in st.session_state:
        st.session_state[key] = default

with tab_xi:
    if not api_key:
        st.info("Inserisci la chiave API-Football nei Secrets di Streamlit.")
        st.stop()
    run = st.button("🔄 AGGIORNA TUTTO E CREA XI", type="primary", use_container_width=True)

if run:
    api = APIFootball(api_key, str(BASE / ".fantacache"))
    ms = MultiSource(str(BASE / ".sourcecache"))
    season = datetime.now().year if datetime.now().month >= 7 else datetime.now().year - 1
    names = roster["name"].astype(str).tolist()

    prog = st.progress(0, text="Lettura Fantacalcio.it...")
    probable_src = fetch_probable_percentages(names) if use_fantacalcio else None
    probable_map = probable_src.percentages if probable_src and probable_src.ok else {}
    probable_teams = getattr(probable_src, "teams", {}) if probable_src and probable_src.ok else {}
    setpiece_src = fetch_set_piece_roles(names) if use_set_pieces else None
    setpiece_map = setpiece_src.roles if setpiece_src and setpiece_src.ok else {}

    prog.progress(.06, text="Calendario della prossima giornata di Serie A...")
    try:
        serie_a_fixtures = next_serie_a_fixtures(api, season, 30)
    except Exception:
        serie_a_fixtures = []

    ctx = []
    prog.progress(.10, text="Risoluzione giocatori API...")
    for idx, r in roster.iterrows():
        name = str(r["name"]).strip()
        role = str(r["role"]).strip().upper()
        key = norm(name)
        team_hint = probable_teams.get(key) or ROSTER_TEAM_HINTS.get(key, "")
        pid = int(r["api_player_id"]) if pd.notna(r.get("api_player_id")) else None
        resolved, conf, err = name, (100 if pid else 0), ""
        row, sb = None, None
        try:
            if pid:
                row = api.player_stats(pid, season)
            else:
                cached_id = api.cached_player_id(name)
                if cached_id:
                    pid = int(cached_id["id"])
                    resolved = cached_id.get("name") or name
                    conf = int(cached_id.get("confidence") or 90)
                    row = api.player_stats(pid, season)
                else:
                    row, conf = api.search_player_stats(name, season, team_hint=team_hint)
                    if row:
                        pid = (row.get("player") or {}).get("id")
            sb = choose_stat_block(row, team_hint)
            if row and row.get("player"):
                resolved = row["player"].get("name") or resolved
        except Exception as exc:
            err = str(exc)

        api_team_id = ((sb or {}).get("team") or {}).get("id")
        league_fixture = find_serie_a_fixture(serie_a_fixtures, team_hint, api_team_id)
        schedule_tid = team_id_from_fixture(league_fixture, team_hint, api_team_id)

        if not sb:
            sb = {"team": {"name": team_hint or "—", "id": schedule_tid}}
        else:
            sb = copy.deepcopy(sb)
            if league_fixture and schedule_tid:
                sb["team"] = {
                    **(sb.get("team") or {}),
                    "id": schedule_tid,
                    "name": team_hint or (sb.get("team") or {}).get("name", "—"),
                }

        ctx.append({
            "name": name, "role": role, "pid": pid, "resolved": resolved,
            "conf": conf, "row": row, "sb": sb, "err": err,
            "team_hint": team_hint, "fixture": league_fixture, "team_id": schedule_tid,
        })
        prog.progress(.10 + .30 * (idx + 1) / len(roster), text=f"API giocatori: {idx+1}/{len(roster)}")

    player_source_rows = [
        {"name": p["name"], "team": p.get("team_hint") or ((p.get("sb") or {}).get("team") or {}).get("name", "")}
        for p in ctx
    ]

    prog.progress(.42, text="Incrocio Gazzetta, Sky e news...")
    gazzetta_map = ms.gazzetta_signals(player_source_rows) if use_gazzetta else {}
    prog.progress(.47)
    sky_map = ms.sky_signals(player_source_rows) if use_sky else {}
    prog.progress(.52)
    news_map = ms.news_for_players(names) if use_news else {norm(x): [] for x in names}

    manual = {}
    for _, r in overrides.iterrows():
        manual[norm(str(r["name"]))] = {
            "probabile_pct": maybe_float(r.get("probabile_pct")),
            "xg90": maybe_float(r.get("xg90")),
            "xa90": maybe_float(r.get("xa90")),
            "note": "" if pd.isna(r.get("note")) else str(r.get("note")),
        }

    fixture_ids = list(dict.fromkeys([
        (p.get("fixture") or {}).get("fixture", {}).get("id")
        for p in ctx if p.get("fixture")
    ]))
    try:
        inj_list = api.injuries_for_fixtures(fixture_ids)
    except Exception:
        inj_list = []
    injuries = {
        (x.get("player") or {}).get("id"): x
        for x in inj_list if (x.get("player") or {}).get("id")
    }

    predictions = {}
    if use_predictions:
        for fid in fixture_ids:
            try:
                predictions[fid] = api.prediction(fid)
            except Exception:
                predictions[fid] = None

    official = {}
    if official_check:
        for p in ctx:
            fx = p.get("fixture")
            fid = ((fx or {}).get("fixture") or {}).get("id")
            if fid and fid not in official and api.is_close_to_kickoff(fx):
                try:
                    official[fid] = api.fixture_details(fid)
                except Exception:
                    official[fid] = None

    team_windows = {}
    if use_cups:
        unique_teams = {}
        for p in ctx:
            if p.get("team_id") and p.get("fixture"):
                unique_teams.setdefault(int(p["team_id"]), p["fixture"])
        total = max(1, len(unique_teams))
        for j, (tid, fx) in enumerate(unique_teams.items()):
            team_windows[tid] = cup_window(api, tid, fx, days=5)
            prog.progress(.55 + .15 * (j + 1) / total, text=f"Controllo coppe: {j+1}/{total}")

    recent_by_player = {}
    if advanced_recent:
        team_recent, recent_ids = {}, []
        tids = sorted({p["team_id"] for p in ctx if p.get("team_id")})
        for tid in tids:
            try:
                team_recent[tid] = api.recent_team_fixtures(tid, season, 5)
            except Exception:
                team_recent[tid] = []
            recent_ids += [
                (x.get("fixture") or {}).get("id")
                for x in team_recent[tid] if (x.get("fixture") or {}).get("id")
            ]
        try:
            details = api.fixtures_details_batch(recent_ids)
        except Exception:
            details = {}
        for p in ctx:
            ids = [(x.get("fixture") or {}).get("id") for x in team_recent.get(p.get("team_id"), [])]
            if p.get("pid"):
                recent_by_player[p["pid"]] = api.recent_player_stats_from_details(
                    [details[x] for x in ids if x in details], p["pid"]
                )

    prog.progress(.73, text="Calcolo schierabilità e rischio turnover...")
    evaluations, source_debug, cup_debug = [], {}, {}
    for i, p in enumerate(ctx):
        key = norm(p["name"])
        fx = p.get("fixture")
        fid = ((fx or {}).get("fixture") or {}).get("id")
        official_status = "unknown"
        if fid and p.get("pid") and official.get(fid):
            official_status = api.official_lineup_status(official[fid], p["pid"])

        m = manual.get(key, {})
        fantapct = m.get("probabile_pct")
        if fantapct is None:
            fantapct = probable_map.get(key)

        ext = []
        for signal in (gazzetta_map.get(key), sky_map.get(key)):
            if signal:
                ext.append(signal.to_dict())

        nitems = news_map.get(key, [])
        news_adj, news_notes = ms.news_adjustment(nitems)

        cup_adj, cup_note = 0.0, ""
        if use_cups and p.get("team_id"):
            cup_adj, cup_note = cup_adjustment(
                api, team_windows.get(int(p["team_id"]), {}), fx, p.get("pid")
            )

        combined_note = "; ".join(x for x in [m.get("note", "").strip(), f"coppe: {cup_note}" if cup_note else ""] if x)

        ev = evaluate_player(
            name=p["name"], role=p["role"], api_player_id=p.get("pid"),
            resolved_name=p["resolved"], resolve_confidence=p["conf"],
            stat_block=p.get("sb"), fixture=fx, injury=injuries.get(p.get("pid")),
            official_status=official_status, prediction=predictions.get(fid),
            probable_pct=fantapct, recent_matches=recent_by_player.get(p.get("pid"), []),
            set_piece_role=setpiece_map.get(key), xg90=m.get("xg90"), xa90=m.get("xa90"),
            manual_note=combined_note, external_source_estimates=ext,
            news_adjustment=news_adj, news_notes=news_notes,
        )

        if cup_adj and official_status != "starter":
            scale = 0.65 if ev.source_count >= 2 else 0.80 if ev.source_count == 1 else 1.0
            adj = cup_adj * scale
            ev.p_starter = round(clamp(ev.p_starter + adj), 1)
            ev.p_vote = round(clamp(ev.p_vote + adj * 0.65), 1)
            ev.schierabilita = round(clamp(ev.schierabilita + adj * 0.60), 1)
            ev.label = status_label(ev.schierabilita)

        if p.get("err"):
            ev.reason += "; dati API incompleti"

        evaluations.append(ev)
        source_debug[key] = {
            "api_error": p.get("err", ""),
            "api_id": p.get("pid"),
            "api_resolved": p.get("resolved", ""),
            "api_confidence": p.get("conf", 0),
            "fantacalcio_pct": fantapct,
            "gazzetta": gazzetta_map.get(key).to_dict() if gazzetta_map.get(key) else None,
            "sky": sky_map.get(key).to_dict() if sky_map.get(key) else None,
            "news_adjustment": news_adj,
        }
        cup_debug[key] = {"adjustment": cup_adj, "note": cup_note}
        prog.progress(.73 + .27 * (i + 1) / len(ctx))

    fantacalcio_found = sum(1 for n in names if norm(n) in probable_map)
    resolved_ok = sum(1 for p in ctx if p.get("pid") and p.get("conf", 0) >= 75)
    cup_teams = sum(1 for x in team_windows.values() if x.get("before") or x.get("after"))

    st.session_state.evaluations_v33 = evaluations
    st.session_state.v33_sources = {
        "api_diag": api.diagnostics(),
        "api_players_resolved": resolved_ok,
        "fantacalcio_found": fantacalcio_found,
        "gazzetta_found": len(gazzetta_map),
        "sky_found": len(sky_map),
        "news_found": sum(len(x) for x in news_map.values()),
        "cup_teams": cup_teams,
        "serie_a_fixtures": len(serie_a_fixtures),
        "debug": source_debug,
    }
    st.session_state.v33_news = {key: [x.to_dict() for x in vals] for key, vals in news_map.items()}
    st.session_state.v33_cups = cup_debug
    prog.empty()
    st.rerun()

evaluations = st.session_state.evaluations_v33
status = st.session_state.v33_sources
news_saved = st.session_state.v33_news
cup_saved = st.session_state.v33_cups

with tab_xi:
    if evaluations:
        choice = best_lineup(evaluations)
        starters = choice["players"]
        subs = bench(evaluations, starters)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Modulo", choice["formation"])
        m2.metric("Indice XI", f'{choice["total"]/11:.0f}/100')
        total_signals = status.get("fantacalcio_found", 0) + status.get("gazzetta_found", 0) + status.get("sky_found", 0)
        m3.metric("Segnali probabili", total_signals)
        m4.metric("Squadre con coppe ±5gg", status.get("cup_teams", 0))

        for role, title in [("P","PORTIERE"),("D","DIFENSORI"),("C","CENTROCAMPISTI"),("A","ATTACCANTI")]:
            group = sorted([x for x in starters if x.role == role], key=lambda x: x.schierabilita, reverse=True)
            if not group:
                continue
            st.markdown(f"### {title}")
            for p in group:
                consensus = (
                    f'<span class="badge">Consenso {p.source_consensus_pct:.0f}%</span>'
                    if p.source_consensus_pct is not None else ""
                )
                cup_note = cup_saved.get(norm(p.name), {}).get("note", "")
                st.markdown(f"""
<div class="player-card">
  <div class="player-title">{esc(p.label)} · {esc(p.name.title())} — {p.schierabilita:.0f}%</div>
  <div class="player-meta">{esc(p.team)} vs {esc(p.opponent)} · {esc(p.home_away)}</div>
  <div class="player-date">📅 Serie A: {esc(format_kickoff(p.kickoff))}</div>
  <div>
    <span class="badge">Titolare {p.p_starter:.0f}%</span>
    <span class="badge">Voto {p.p_vote:.0f}%</span>
    {consensus}
    <span class="badge">{p.source_count} fonti</span>
    <span class="badge">Confidenza {esc(p.confidence)}</span>
  </div>
  <div class="player-reason">{esc(p.reason)}</div>
  <div class="source">{esc(p.source_detail)}</div>
  {"<div class='cup'>🏆 " + esc(cup_note) + "</div>" if cup_note else ""}
</div>
""", unsafe_allow_html=True)

        st.markdown("### 🪑 Panchina")
        for i, p in enumerate(subs, 1):
            st.markdown(
                f"**{i}. {p.name.title()} ({p.role})** — {p.schierabilita:.0f}% · "
                f"titolare {p.p_starter:.0f}% · **voto {p.p_vote:.0f}%**  \n"
                f"{p.team} vs {p.opponent} · {format_kickoff(p.kickoff)}  \n{p.reason}"
            )

with tab_sources:
    if not evaluations:
        st.info("Aggiorna prima la formazione.")
    else:
        d = status.get("api_diag", {})
        st.subheader("📡 Diagnostica API-Football")
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Chiamate rete", d.get("network_calls", 0))
        a2.metric("Da cache", d.get("cache_hits", 0))
        a3.metric("Quota giorno", f"{d.get('daily_remaining') or '—'}/{d.get('daily_limit') or '100'}")
        a4.metric("Quota minuto", f"{d.get('minute_remaining') or '—'}/{d.get('minute_limit') or '10'}")
        if d.get("errors"):
            with st.expander(f"⚠️ Errori API ({d['errors']})"):
                for msg in d.get("last_errors", []):
                    st.code(msg)

        st.subheader("🗞️ Copertura")
        f1, f2, f3, f4 = st.columns(4)
        f1.metric("Fantacalcio.it", status.get("fantacalcio_found", 0))
        f2.metric("Gazzetta", status.get("gazzetta_found", 0))
        f3.metric("Sky Sport", status.get("sky_found", 0))
        f4.metric("News", status.get("news_found", 0))
        st.caption(
            f"Giocatori API abbinati ≥75%: {status.get('api_players_resolved',0)}/{len(evaluations)} · "
            f"fixture Serie A lette: {status.get('serie_a_fixtures',0)}"
        )

        debug = status.get("debug", {})
        for p in sorted(evaluations, key=lambda x: x.schierabilita, reverse=True):
            key = norm(p.name)
            info = debug.get(key, {})
            cup = cup_saved.get(key, {})
            with st.expander(f"{p.name.title()} · voto {p.p_vote:.0f}% · titolare {p.p_starter:.0f}%"):
                st.write("**Prossima Serie A:**", f"{p.team} vs {p.opponent} · {format_kickoff(p.kickoff)} · {p.home_away}")
                st.write("**Consenso:**", p.source_detail or "Nessun segnale editoriale")
                if cup.get("note"):
                    st.write("**Coppe/turnover:**", cup["note"], f"(aggiustamento {cup['adjustment']:+.1f})")
                if info.get("api_id"):
                    st.caption(
                        f"API player ID {info['api_id']} · {info.get('api_resolved','')} · "
                        f"confidenza {info.get('api_confidence',0)}%"
                    )
                if info.get("api_error"):
                    st.error(info["api_error"])
                items = news_saved.get(key, [])
                for item in items:
                    pol = "🔴" if item["polarity"] < 0 else "🟢" if item["polarity"] > 0 else "⚪"
                    st.markdown(f'{pol} [{item["title"]}]({item["url"]}) · {item["source"]}')

with tab_all:
    if evaluations:
        df = pd.DataFrame([x.to_dict() for x in evaluations]).rename(columns={
            "name":"Giocatore","role":"R","team":"Squadra","opponent":"Avversario",
            "kickoff":"Data/ora","p_starter":"% titolare","p_vote":"% voto",
            "source_consensus_pct":"Consenso fonti","source_count":"N. fonti",
            "schierabilita":"% schierabilità","label":"Giudizio","reason":"Motivo"
        })
        if "Data/ora" in df:
            df["Data/ora"] = df["Data/ora"].map(format_kickoff)
        cols = [
            "Giocatore","R","Squadra","Avversario","Data/ora","% titolare","% voto",
            "Consenso fonti","N. fonti","% schierabilità","Giudizio","Motivo"
        ]
        st.dataframe(df[[c for c in cols if c in df.columns]], hide_index=True, use_container_width=True)
    else:
        st.info("Aggiorna prima la formazione.")
