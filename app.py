from __future__ import annotations

import html
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from api_football import APIFootball
from fantacalcio_sources import fetch_probable_percentages, fetch_set_piece_roles, norm
from fantasy_engine import evaluate_player, best_lineup, bench
from multi_source import MultiSource

APP_VERSION = "3.1"
load_dotenv()
BASE = Path(__file__).resolve().parent
ROSTER = BASE / "roster.csv"
OVERRIDES = BASE / "manual_overrides.csv"

st.set_page_config(page_title=f"Fanta XI V{APP_VERSION}", page_icon="⚽", layout="wide")
st.markdown("""
<style>
.block-container{padding-top:.8rem;padding-bottom:3rem;max-width:1180px}
h1{font-size:clamp(1.7rem,7vw,2.5rem)!important}
div[data-testid="stMetric"]{border:1px solid rgba(148,163,184,.2);border-radius:16px;padding:10px}
.card{border:1px solid rgba(148,163,184,.22);border-radius:18px;padding:14px;margin:10px 0;background:rgba(15,23,42,.55)}
.title{font-size:1.08rem;font-weight:800}.meta{opacity:.82;font-size:.9rem;margin-top:4px}.reason{margin-top:8px;line-height:1.35}
.badge{display:inline-block;padding:3px 8px;border-radius:99px;border:1px solid rgba(148,163,184,.3);font-size:.77rem;margin:4px 5px 0 0}
.api{padding:.75rem 1rem;border:1px solid rgba(148,163,184,.24);border-radius:14px;margin:.5rem 0 1rem}
@media(max-width:640px){.block-container{padding-left:.75rem;padding-right:.75rem}.card{padding:12px}button{min-height:44px}}
</style>
""", unsafe_allow_html=True)


def secret_key():
    try:
        return st.secrets.get("API_FOOTBALL_KEY", "") or os.getenv("API_FOOTBALL_KEY", "")
    except Exception:
        return os.getenv("API_FOOTBALL_KEY", "")


def fnum(v):
    try:
        return None if pd.isna(v) or v == "" else float(v)
    except Exception:
        return None


def esc(v):
    return html.escape(str(v or ""))


st.title(f"⚽ Fanta XI Assistant V{APP_VERSION}")
st.caption("Fantacalcio.it + Gazzetta + Sky + news + API-Football, ottimizzata per il piano Free.")

with st.expander("⚙️ Impostazioni", expanded=False):
    api_key = st.text_input("API-Football key", value=secret_key(), type="password")
    c1, c2 = st.columns(2)
    use_predictions = c1.toggle("Matchup API", value=False, help="Consuma chiamate extra: sul piano Free è meglio lasciarlo spento normalmente.")
    official_check = c2.toggle("Lineup ufficiali entro 3 ore", value=True)
    c3, c4, c5 = st.columns(3)
    use_fanta = c3.toggle("Fantacalcio.it", value=True)
    use_gazza = c4.toggle("Gazzetta", value=True)
    use_sky = c5.toggle("Sky Sport", value=True)
    use_news = st.toggle("Notizie ultime 4 giorni", value=True)
    use_setpieces = st.toggle("Rigoristi/piazzati", value=True)
    st.caption("Le richieste API vengono rallentate automaticamente e le risposte vengono riutilizzate dalla cache.")

roster = pd.read_csv(ROSTER, dtype={"api_player_id": "Int64"})
overrides = pd.read_csv(OVERRIDES)
tab_xi, tab_sources, tab_all, tab_data = st.tabs(["⭐ XI", "📰 Fonti", "📊 Rosa", "✏️ Dati"])

with tab_data:
    st.subheader("Rosa")
    st.dataframe(roster, hide_index=True, use_container_width=True)
    st.subheader("Override")
    st.dataframe(overrides, hide_index=True, use_container_width=True)

for k, default in [("v31_eval", None), ("v31_status", {}), ("v31_news", {})]:
    if k not in st.session_state:
        st.session_state[k] = default

with tab_xi:
    if not api_key:
        st.info("Inserisci la chiave API nei Secrets di Streamlit.")
        st.stop()
    run = st.button("🔄 AGGIORNA TUTTO E CREA XI", type="primary", use_container_width=True)

if run:
    api = APIFootball(api_key, str(BASE / ".fantacache"))
    ms = MultiSource(str(BASE / ".sourcecache"))
    season = datetime.now().year if datetime.now().month >= 7 else datetime.now().year - 1
    names = roster["name"].astype(str).tolist()
    prog = st.progress(0, text="Giocatori e statistiche API...")

    ctx = []
    for i, r in roster.iterrows():
        name, role = str(r["name"]).strip(), str(r["role"]).strip().upper()
        pid = int(r["api_player_id"]) if pd.notna(r.get("api_player_id")) else None
        resolved, conf, err, row, sb = name, (100 if pid else 0), "", None, None
        try:
            if not pid:
                p, conf = api.search_profile(name)
                if p:
                    pid, resolved = p.get("id"), p.get("name") or name
            row = api.player_stats(pid, season) if pid else None
            sb = api.best_stat_block(row)
            if row:
                resolved = (row.get("player") or {}).get("name") or resolved
            if not pid:
                err = "giocatore non trovato nell'API"
        except Exception as ex:
            err = str(ex)
        ctx.append({"name": name, "role": role, "pid": pid, "resolved": resolved, "conf": conf, "sb": sb, "err": err})
        prog.progress(.38 * (i + 1) / len(roster), text=f"Giocatori API {i+1}/{len(roster)}")

    team_ids = sorted({((p.get("sb") or {}).get("team") or {}).get("id") for p in ctx if ((p.get("sb") or {}).get("team") or {}).get("id")})
    fixtures = {}
    for i, tid in enumerate(team_ids):
        try:
            fixtures[tid] = api.next_fixture(tid)
        except Exception:
            fixtures[tid] = None
        prog.progress(.38 + .12 * (i + 1) / max(1, len(team_ids)), text="Prossime partite...")

    source_rows = [{"name": p["name"], "team": ((p.get("sb") or {}).get("team") or {}).get("name", "")} for p in ctx]
    prog.progress(.52, text="Fonti editoriali...")
    fanta_src = fetch_probable_percentages(names) if use_fanta else None
    fanta_map = fanta_src.percentages if fanta_src and fanta_src.ok else {}
    set_src = fetch_set_piece_roles(names) if use_setpieces else None
    set_map = set_src.roles if set_src and set_src.ok else {}
    gazza_map = ms.gazzetta_signals(source_rows) if use_gazza else {}
    prog.progress(.62)
    sky_map = ms.sky_signals(source_rows) if use_sky else {}
    prog.progress(.69)
    news_map = ms.news_for_players(names) if use_news else {norm(n): [] for n in names}

    manual = {}
    for _, r in overrides.iterrows():
        manual[norm(str(r["name"]))] = {"probabile_pct": fnum(r.get("probabile_pct")), "xg90": fnum(r.get("xg90")), "xa90": fnum(r.get("xa90")), "note": "" if pd.isna(r.get("note")) else str(r.get("note"))}

    fixture_ids = sorted({(fx.get("fixture") or {}).get("id") for fx in fixtures.values() if fx and (fx.get("fixture") or {}).get("id")})
    try:
        inj = api.injuries_for_fixtures(fixture_ids)
    except Exception:
        inj = []
    injuries = {(x.get("player") or {}).get("id"): x for x in inj if (x.get("player") or {}).get("id")}

    predictions = {}
    if use_predictions:
        for fid in fixture_ids:
            try:
                predictions[fid] = api.prediction(fid)
            except Exception:
                predictions[fid] = None

    official = {}
    if official_check:
        for fx in fixtures.values():
            if fx and api.is_close_to_kickoff(fx):
                fid = (fx.get("fixture") or {}).get("id")
                try:
                    official[fid] = api.fixture_details(fid)
                except Exception:
                    pass

    evaluations, debug, api_issues = [], {}, 0
    for i, p in enumerate(ctx):
        sb = p.get("sb") or {}
        tid = (sb.get("team") or {}).get("id")
        fx = fixtures.get(tid)
        fid = ((fx or {}).get("fixture") or {}).get("id")
        off = api.official_lineup_status(official[fid], p["pid"]) if fid in official and p.get("pid") else "unknown"
        key = norm(p["name"])
        m = manual.get(key, {})
        fpct = m.get("probabile_pct") if m.get("probabile_pct") is not None else fanta_map.get(key)
        ext = [s.to_dict() for s in (gazza_map.get(key), sky_map.get(key)) if s]
        nitems = news_map.get(key, [])
        nadj, nnotes = ms.news_adjustment(nitems)
        ev = evaluate_player(name=p["name"], role=p["role"], api_player_id=p.get("pid"), resolved_name=p["resolved"], resolve_confidence=p["conf"], stat_block=sb, fixture=fx, injury=injuries.get(p.get("pid")), official_status=off, prediction=predictions.get(fid), probable_pct=fpct, recent_matches=[], set_piece_role=set_map.get(key), xg90=m.get("xg90"), xa90=m.get("xa90"), manual_note=m.get("note", ""), external_source_estimates=ext, news_adjustment=nadj, news_notes=nnotes)
        if p["err"]:
            api_issues += 1
            ev.reason += "; dati API incompleti"
        evaluations.append(ev)
        debug[key] = {"api_name": p["resolved"], "api_confidence": p["conf"], "api_error": p["err"]}
        prog.progress(.78 + .22 * (i + 1) / len(ctx), text="Calcolo XI...")

    st.session_state.v31_eval = evaluations
    st.session_state.v31_status = {"api": api.diagnostics(), "api_issues": api_issues, "fantacalcio": len(fanta_map), "gazzetta": len(gazza_map), "sky": len(sky_map), "news": sum(len(v) for v in news_map.values()), "debug": debug}
    st.session_state.v31_news = {k: [x.to_dict() for x in v] for k, v in news_map.items()}
    prog.empty()
    st.rerun()

evaluations = st.session_state.v31_eval
status = st.session_state.v31_status
news_saved = st.session_state.v31_news

with tab_xi:
    if evaluations:
        choice = best_lineup(evaluations)
        starters = choice["players"]
        subs = bench(evaluations, starters)
        d = status.get("api", {})
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Modulo", choice["formation"])
        c2.metric("Indice XI", f'{choice["total"]/11:.0f}/100')
        c3.metric("Segnali probabili", status.get("fantacalcio",0)+status.get("gazzetta",0)+status.get("sky",0))
        c4.metric("News", status.get("news",0))
        st.markdown(f'<div class="api"><b>API-Football</b> · {d.get("network_calls",0)} chiamate · {d.get("cache_hits",0)} cache · quota oggi {d.get("daily_remaining","—")}/{d.get("daily_limit","—")} · minuto {d.get("minute_remaining","—")}/{d.get("minute_limit","—")} · attesa automatica {d.get("wait_seconds",0)}s</div>', unsafe_allow_html=True)
        if status.get("api_issues"):
            st.warning(f'{status["api_issues"]} giocatori hanno dati API incompleti: nella scheda Fonti trovi il motivo.')

        for role, title in [("P","PORTIERE"),("D","DIFENSORI"),("C","CENTROCAMPISTI"),("A","ATTACCANTI")]:
            group = sorted([x for x in starters if x.role == role], key=lambda x: x.schierabilita, reverse=True)
            if not group:
                continue
            st.markdown(f"### {title}")
            for p in group:
                cons = f'<span class="badge">Consenso {p.source_consensus_pct:.0f}%</span>' if p.source_consensus_pct is not None else ""
                st.markdown(f'<div class="card"><div class="title">{esc(p.label)} · {esc(p.name.title())} — {p.schierabilita:.0f}%</div><div class="meta">{esc(p.team)} vs {esc(p.opponent)} · {esc(p.home_away)}</div><div><span class="badge">Titolare {p.p_starter:.0f}%</span>{cons}<span class="badge">{p.source_count} fonti</span><span class="badge">Confidenza {esc(p.confidence)}</span></div><div class="reason">{esc(p.reason)}</div><div class="meta">{esc(p.source_detail)}</div></div>', unsafe_allow_html=True)

        st.markdown("### 🪑 Panchina")
        for i, p in enumerate(subs, 1):
            st.markdown(f"**{i}. {p.name.title()} ({p.role})** — {p.schierabilita:.0f}% · titolare {p.p_starter:.0f}%  \n{p.reason}")

with tab_sources:
    if not evaluations:
        st.info("Aggiorna prima la formazione.")
    else:
        a,b,c,d = st.columns(4)
        a.metric("Fantacalcio.it", status.get("fantacalcio",0)); b.metric("Gazzetta", status.get("gazzetta",0)); c.metric("Sky", status.get("sky",0)); d.metric("News", status.get("news",0))
        for p in sorted(evaluations, key=lambda x:x.schierabilita, reverse=True):
            key = norm(p.name); dbg = status.get("debug",{}).get(key,{})
            with st.expander(f"{p.name.title()} · {p.p_starter:.0f}% titolare"):
                st.write("**Abbinamento API:**", dbg.get("api_name") or "—")
                st.write("**Confidenza nome:**", f'{dbg.get("api_confidence",0)}%')
                if dbg.get("api_error"):
                    st.warning(dbg["api_error"])
                st.write("**Fonti:**", p.source_detail or "Nessun segnale editoriale")
                for item in news_saved.get(key,[]):
                    st.markdown(f'[{item["title"]}]({item["url"]}) · {item["source"]}')

with tab_all:
    if evaluations:
        df = pd.DataFrame([x.to_dict() for x in evaluations])
        st.dataframe(df[["name","role","team","opponent","p_starter","source_count","schierabilita","label","confidence","reason"]], hide_index=True, use_container_width=True)
    else:
        st.info("Aggiorna prima la formazione.")
