from __future__ import annotations

import html
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from api_football import APIFootball
from fantacalcio_sources import (
    PROBABILI_URL,
    RIGORISTI_URL,
    fetch_probable_percentages,
    fetch_set_piece_roles,
    norm,
)
from fantasy_engine import evaluate_player, best_lineup, bench
from multi_source import MultiSource, GAZZETTA_PROB_URL

APP_VERSION = "3.1"
load_dotenv()
BASE = Path(__file__).resolve().parent
ROSTER = BASE / "roster.csv"
OVERRIDES = BASE / "manual_overrides.csv"

st.set_page_config(page_title=f"Fanta XI V{APP_VERSION}", page_icon="⚽", layout="wide")
st.markdown(
    """
<style>
.block-container {padding-top:.8rem;padding-bottom:3rem;max-width:1180px}
h1 {font-size:clamp(1.7rem,7vw,2.5rem)!important}
div[data-testid="stMetric"] {border:1px solid rgba(148,163,184,.20);border-radius:16px;padding:10px}
.player-card {border:1px solid rgba(148,163,184,.22);border-radius:18px;padding:14px;margin:10px 0;background:rgba(15,23,42,.55)}
.player-title {font-size:1.08rem;font-weight:800}.player-meta{opacity:.82;font-size:.9rem;margin-top:4px}
.player-reason{margin-top:8px;line-height:1.35}.badge{display:inline-block;padding:3px 8px;border-radius:99px;border:1px solid rgba(148,163,184,.3);font-size:.77rem;margin:4px 5px 0 0}.source{opacity:.88;font-size:.82rem;margin-top:7px}
.api-ok{padding:.8rem 1rem;border:1px solid rgba(148,163,184,.24);border-radius:14px;margin:.5rem 0 1rem 0}
@media(max-width:640px){.block-container{padding-left:.75rem;padding-right:.75rem}.player-card{padding:12px}button{min-height:44px}}
</style>
""",
    unsafe_allow_html=True,
)


def secret_key() -> str:
    try:
        return st.secrets.get("API_FOOTBALL_KEY", "") or os.getenv("API_FOOTBALL_KEY", "")
    except Exception:
        return os.getenv("API_FOOTBALL_KEY", "")


def maybe_float(v):
    try:
        if pd.isna(v) or v == "":
            return None
        return float(v)
    except Exception:
        return None


def esc(x):
    return html.escape(str(x or ""))


def friendly_api_error(msg: str) -> str:
    low = (msg or "").lower()
    if "limite" in low or "429" in low or "rate" in low:
        return "limite API temporaneo"
    if "chiave" in low or "401" in low or "403" in low:
        return "problema con la chiave API"
    if "timeout" in low:
        return "API lenta / timeout"
    return "dati API non disponibili"


def fmt_quota(v, fallback="—"):
    return fallback if v is None else str(v)


st.title(f"⚽ Fanta XI Assistant V{APP_VERSION}")
st.caption("Consenso multi-fonte + API-Football ottimizzata per il piano Free.")

with st.expander("⚙️ Impostazioni", expanded=False):
    api_key = st.text_input("API-Football key", value=secret_key(), type="password")
    c1, c2 = st.columns(2)
    use_predictions = c1.toggle(
        "Matchup API",
        value=False,
        help="Usa una chiamata aggiuntiva per ogni partita. Sul piano Free conviene attivarlo solo quando serve.",
    )
    official_check = c2.toggle("Lineup ufficiali entro 3 ore", value=True)
    st.markdown("**Fonti probabili / news**")
    s1, s2, s3 = st.columns(3)
    use_fantacalcio = s1.toggle("Fantacalcio.it", value=True)
    use_gazzetta = s2.toggle("Gazzetta", value=True)
    use_sky = s3.toggle("Sky Sport", value=True)
    use_news = st.toggle("Notizie ultime 4 giorni", value=True)
    use_set_pieces = st.toggle("Rigoristi/piazzati Fantacalcio.it", value=True)
    advanced_recent = st.toggle(
        "Ultime 5 avanzate",
        value=False,
        help="Molto più costosa in richieste API: sul piano Free lasciala spenta per l'uso normale.",
    )
    clear_cache = st.button("Svuota cache API-Football")
    st.caption("V3.1 limita automaticamente la velocità delle richieste e riusa la cache per non superare il piano Free.")

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
    st.caption("Su Streamlit Cloud le modifiche ai file locali possono perdersi dopo un redeploy o riavvio del container.")
    st.subheader("Override opzionali")
    over_edit = st.data_editor(overrides, use_container_width=True, hide_index=True)
    if st.button("💾 Salva override"):
        over_edit.to_csv(OVERRIDES, index=False)
        st.success("Override salvati per questa istanza dell'app.")

with tab_info:
    st.markdown(
        """
### Come lavora la V3.1
La priorità è: **formazione ufficiale → indisponibilità certe → probabili editoriali → news recenti → statistiche → matchup/bonus**.

La V3.1 riduce le chiamate API in tre modi: cerca giocatore e statistiche stagionali in una sola richiesta, scarica le prossime partite raggruppandole per campionato invece che una squadra alla volta e conserva le risposte in cache. Inoltre legge i limiti restituiti da API-Football e rallenta automaticamente quando il piano Free è vicino al limite per minuto.

Una stima interna di Gazzetta/Sky non viene presentata come percentuale ufficiale della testata. Se le fonti sono molto discordanti, la confidenza viene ridotta.
"""
    )
    st.link_button("Fantacalcio.it probabili", PROBABILI_URL)
    st.link_button("Gazzetta probabili", GAZZETTA_PROB_URL)
    st.link_button("Fantacalcio.it rigoristi", RIGORISTI_URL)

if clear_cache and api_key:
    APIFootball(api_key, str(BASE / ".fantacache")).cache.clear()
    st.toast("Cache API svuotata")

for key, default in [
    ("evaluations_v31", None),
    ("v31_sources", {}),
    ("v31_news", {}),
]:
    if key not in st.session_state:
        st.session_state[key] = default

with tab_xi:
    if not api_key:
        st.info("Inserisci la chiave API nei Secrets di Streamlit o nelle Impostazioni.")
        st.stop()
    run = st.button("🔄 AGGIORNA TUTTO E CREA XI", type="primary", use_container_width=True)

if run:
    api = APIFootball(api_key, str(BASE / ".fantacache"))
    ms = MultiSource(str(BASE / ".sourcecache"))
    season = datetime.now().year if datetime.now().month >= 7 else datetime.now().year - 1
    names = roster["name"].astype(str).tolist()
    prog = st.progress(0, text="Risoluzione giocatori e statistiche...")

    ctx = []
    for idx, r in roster.iterrows():
        name = str(r["name"]).strip()
        role = str(r["role"]).strip().upper()
        pid = int(r["api_player_id"]) if pd.notna(r.get("api_player_id")) else None
        resolved, conf, err = name, (100 if pid else 0), ""
        row, sb = None, None
        try:
            if pid:
                row = api.player_stats(pid, season)
                conf = 100
            else:
                row, conf = api.resolve_player_with_stats(name, season)
                if row:
                    pid = (row.get("player") or {}).get("id")
            if row:
                resolved = (row.get("player") or {}).get("name") or resolved
                sb = api.best_stat_block(row)
            if not row:
                err = "Nessun giocatore API trovato"
        except Exception as exc:
            err = str(exc)
        ctx.append({
            "name": name, "role": role, "pid": pid, "resolved": resolved,
            "conf": conf, "row": row, "sb": sb, "err": err,
        })
        prog.progress(.35 * (idx + 1) / len(roster), text=f"Giocatori API: {idx+1}/{len(roster)}")

    team_ids = sorted({
        ((p.get("sb") or {}).get("team") or {}).get("id")
        for p in ctx if ((p.get("sb") or {}).get("team") or {}).get("id")
    })
    league_ids = sorted({
        ((p.get("sb") or {}).get("league") or {}).get("id")
        for p in ctx if ((p.get("sb") or {}).get("league") or {}).get("id")
    })

    fixtures_by_team = {tid: None for tid in team_ids}
    try:
        upcoming = api.upcoming_fixtures_by_league(league_ids, season, days=14)
        fixtures_by_team.update(api.map_next_fixture_by_team(upcoming, team_ids))
    except Exception:
        upcoming = []
    prog.progress(.43, text="Partite recuperate per campionato")

    player_source_rows = []
    for p in ctx:
        sb = p.get("sb") or {}
        player_source_rows.append({
            "name": p["name"],
            "team": (sb.get("team") or {}).get("name", ""),
        })

    prog.progress(.46, text="Incrocio Fantacalcio, Gazzetta e Sky...")
    probable_src = fetch_probable_percentages(names) if use_fantacalcio else None
    probable_map = probable_src.percentages if probable_src and probable_src.ok else {}
    setpiece_src = fetch_set_piece_roles(names) if use_set_pieces else None
    setpiece_map = setpiece_src.roles if setpiece_src and setpiece_src.ok else {}
    prog.progress(.53)
    gazzetta_map = ms.gazzetta_signals(player_source_rows) if use_gazzetta else {}
    prog.progress(.60)
    sky_map = ms.sky_signals(player_source_rows) if use_sky else {}
    prog.progress(.67)
    news_map = ms.news_for_players(names) if use_news else {norm(x): [] for x in names}
    prog.progress(.72)

    manual = {}
    for _, r in overrides.iterrows():
        manual[norm(str(r["name"]))] = {
            "probabile_pct": maybe_float(r.get("probabile_pct")),
            "xg90": maybe_float(r.get("xg90")),
            "xa90": maybe_float(r.get("xa90")),
            "note": "" if pd.isna(r.get("note")) else str(r.get("note")),
        }

    fixture_ids = sorted({
        (fx.get("fixture") or {}).get("id")
        for fx in fixtures_by_team.values() if fx and (fx.get("fixture") or {}).get("id")
    })
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
        for j, fid in enumerate(fixture_ids):
            try:
                predictions[fid] = api.prediction(fid)
            except Exception:
                predictions[fid] = None
            prog.progress(.73 + .07 * (j + 1) / max(1, len(fixture_ids)), text="Matchup API...")

    official = {}
    if official_check:
        for fx in fixtures_by_team.values():
            if fx and api.is_close_to_kickoff(fx):
                fid = (fx.get("fixture") or {}).get("id")
                try:
                    official[fid] = api.fixture_details(fid)
                except Exception:
                    official[fid] = None

    recent_by_player = {}
    if advanced_recent:
        team_recent, recent_ids = {}, []
        for tid in team_ids:
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
            tid = ((p.get("sb") or {}).get("team") or {}).get("id")
            ids = [(x.get("fixture") or {}).get("id") for x in team_recent.get(tid, [])]
            if p.get("pid"):
                recent_by_player[p["pid"]] = api.recent_player_stats_from_details(
                    [details[x] for x in ids if x in details], p["pid"]
                )

    prog.progress(.82, text="Calcolo formazione...")
    evaluations, source_debug = [], {}
    api_issue_count = 0
    uncertain_match_count = 0

    for i, p in enumerate(ctx):
        sb = p.get("sb") or {}
        tid = (sb.get("team") or {}).get("id")
        fx = fixtures_by_team.get(tid)
        fid = ((fx or {}).get("fixture") or {}).get("id")
        official_status = "unknown"
        if fid and p.get("pid") and official.get(fid):
            official_status = api.official_lineup_status(official[fid], p["pid"])

        key = norm(p["name"])
        m = manual.get(key, {})
        fantapct = m.get("probabile_pct")
        if fantapct is None:
            fantapct = probable_map.get(key)

        ext = []
        for sig in [gazzetta_map.get(key), sky_map.get(key)]:
            if sig:
                ext.append(sig.to_dict())

        nitems = news_map.get(key, [])
        nadj, nnotes = ms.news_adjustment(nitems)
        ev = evaluate_player(
            name=p["name"], role=p["role"], api_player_id=p.get("pid"),
            resolved_name=p["resolved"], resolve_confidence=p["conf"], stat_block=sb,
            fixture=fx, injury=injuries.get(p.get("pid")), official_status=official_status,
            prediction=predictions.get(fid), probable_pct=fantapct,
            recent_matches=recent_by_player.get(p.get("pid"), []),
            set_piece_role=setpiece_map.get(key), xg90=m.get("xg90"), xa90=m.get("xa90"),
            manual_note=m.get("note", ""), external_source_estimates=ext,
            news_adjustment=nadj, news_notes=nnotes,
        )
        if p.get("err"):
            api_issue_count += 1
            ev.reason += f"; {friendly_api_error(p['err'])}"
        if p.get("conf", 0) < 75:
            uncertain_match_count += 1
        evaluations.append(ev)
        source_debug[key] = {
            "api_name": p["resolved"], "api_confidence": p["conf"], "api_error": p["err"],
            "fantacalcio_pct": fantapct,
            "gazzetta": gazzetta_map.get(key).to_dict() if gazzetta_map.get(key) else None,
            "sky": sky_map.get(key).to_dict() if sky_map.get(key) else None,
            "news_adjustment": nadj,
        }
        prog.progress(.82 + .18 * (i + 1) / len(ctx))

    diag = api.diagnostics()
    st.session_state.evaluations_v31 = evaluations
    st.session_state.v31_sources = {
        "api": diag,
        "api_issue_count": api_issue_count,
        "uncertain_match_count": uncertain_match_count,
        "fantacalcio_ok": bool(probable_src and probable_src.ok),
        "fantacalcio_updated": probable_src.updated_at if probable_src else "",
        "fantacalcio_found": len(probable_map),
        "gazzetta_found": len(gazzetta_map),
        "sky_found": len(sky_map),
        "news_found": sum(len(x) for x in news_map.values()),
        "debug": source_debug,
    }
    st.session_state.v31_news = {
        key: [x.to_dict() for x in vals] for key, vals in news_map.items()
    }
    prog.empty()
    st.rerun()


evaluations = st.session_state.evaluations_v31
status = st.session_state.v31_sources
news_saved = st.session_state.v31_news

with tab_xi:
    if evaluations:
        choice = best_lineup(evaluations)
        starters = choice["players"]
        subs = bench(evaluations, starters)
        diag = status.get("api", {})
        source_signals = (
            status.get("fantacalcio_found", 0)
            + status.get("gazzetta_found", 0)
            + status.get("sky_found", 0)
        )

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Modulo", choice["formation"])
        m2.metric("Indice XI", f'{choice["total"]/11:.0f}/100')
        m3.metric("Segnali probabili", source_signals)
        m4.metric("News", status.get("news_found", 0))

        daily_txt = f'{fmt_quota(diag.get("daily_remaining"))}/{fmt_quota(diag.get("daily_limit"))}'
        minute_txt = f'{fmt_quota(diag.get("minute_remaining"))}/{fmt_quota(diag.get("minute_limit"))}'
        st.markdown(
            f'<div class="api-ok"><b>API-Football</b> · {diag.get("network_calls",0)} chiamate rete · '
            f'{diag.get("cache_hits",0)} cache · quota oggi {daily_txt} rimanenti/limite · '
            f'quota minuto {minute_txt} · attesa automatica {diag.get("wait_seconds",0):.0f}s</div>',
            unsafe_allow_html=True,
        )
        if status.get("api_issue_count", 0):
            st.warning(f'{status["api_issue_count"]} giocatori hanno dati API incompleti. Apri “📰 Fonti” per vedere il motivo esatto.')

        for role, title in [("P", "PORTIERE"), ("D", "DIFENSORI"), ("C", "CENTROCAMPISTI"), ("A", "ATTACCANTI")]:
            group = sorted([x for x in starters if x.role == role], key=lambda x: x.schierabilita, reverse=True)
            if not group:
                continue
            st.markdown(f"### {title}")
            for p in group:
                consensus = (
                    f'<span class="badge">Consenso fonti {p.source_consensus_pct:.0f}%</span>'
                    if p.source_consensus_pct is not None else ""
                )
                st.markdown(
                    f"""
<div class="player-card">
  <div class="player-title">{esc(p.label)} · {esc(p.name.title())} — {p.schierabilita:.0f}%</div>
  <div class="player-meta">{esc(p.team)} vs {esc(p.opponent)} · {esc(p.home_away)}</div>
  <div><span class="badge">Titolare {p.p_starter:.0f}%</span>{consensus}<span class="badge">{p.source_count} fonti</span><span class="badge">Confidenza {esc(p.confidence)}</span></div>
  <div class="player-reason">{esc(p.reason)}</div>
  <div class="source">{esc(p.source_detail)}</div>
</div>
""",
                    unsafe_allow_html=True,
                )

        st.markdown("### 🪑 Panchina")
        for i, p in enumerate(subs, 1):
            st.markdown(
                f"**{i}. {p.name.title()} ({p.role})** — {p.schierabilita:.0f}% · titolare {p.p_starter:.0f}%  \n{p.reason}"
            )

with tab_sources:
    if not evaluations:
        st.info("Aggiorna prima la formazione.")
    else:
        a, b, c, d = st.columns(4)
        a.metric("Fantacalcio.it", status.get("fantacalcio_found", 0))
        b.metric("Gazzetta", status.get("gazzetta_found", 0))
        c.metric("Sky", status.get("sky_found", 0))
        d.metric("News", status.get("news_found", 0))

        for p in sorted(evaluations, key=lambda x: x.schierabilita, reverse=True):
            key = norm(p.name)
            dbg = status.get("debug", {}).get(key, {})
            with st.expander(f"{p.name.title()} · {p.p_starter:.0f}% titolare · {p.confidence}"):
                st.write("**Abbinamento API:**", dbg.get("api_name") or "—")
                st.write("**Confidenza nome API:**", f'{dbg.get("api_confidence", 0)}%')
                if dbg.get("api_error"):
                    st.warning(f'API: {dbg["api_error"]}')
                st.write("**Consenso:**", p.source_detail or "Nessun segnale editoriale")
                items = news_saved.get(key, [])
                if items:
                    st.write("**Notizie recenti:**")
                    for item in items:
                        pol = "🔴" if item["polarity"] < 0 else "🟢" if item["polarity"] > 0 else "⚪"
                        st.markdown(f'{pol} [{item["title"]}]({item["url"]}) · {item["source"]}')
                else:
                    st.caption("Nessuna notizia recente associata.")

with tab_all:
    if evaluations:
        df = pd.DataFrame([x.to_dict() for x in evaluations]).rename(columns={
            "name": "Giocatore", "role": "R", "team": "Squadra", "opponent": "Avversario",
            "p_starter": "% titolare", "source_consensus_pct": "Consenso fonti",
            "source_count": "N. fonti", "source_detail": "Dettaglio fonti",
            "schierabilita": "% schierabilità", "label": "Giudizio",
            "recent_rating": "Rating recenti", "threat_score": "Pericolosità",
            "fixture_score": "Matchup", "reason": "Motivo",
        })
        cols = [
            "Giocatore", "R", "Squadra", "Avversario", "% titolare", "Consenso fonti",
            "N. fonti", "% schierabilità", "Giudizio", "Rating recenti",
            "Pericolosità", "Matchup", "Dettaglio fonti", "Motivo",
        ]
        st.dataframe(df[[c for c in cols if c in df.columns]], hide_index=True, use_container_width=True)
    else:
        st.info("Aggiorna prima la formazione.")
