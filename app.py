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

load_dotenv()
BASE = Path(__file__).resolve().parent
ROSTER = BASE / "roster.csv"
OVERRIDES = BASE / "manual_overrides.csv"

st.set_page_config(page_title="Fanta XI V3.1", page_icon="⚽", layout="wide")

st.markdown(
    """
<style>
.block-container {padding-top:.9rem;padding-bottom:3rem;max-width:1180px}
h1 {font-size:clamp(1.7rem,7vw,2.55rem)!important}
div[data-testid="stMetric"] {border:1px solid rgba(148,163,184,.2);border-radius:16px;padding:10px}
.player-card {border:1px solid rgba(148,163,184,.22);border-radius:18px;padding:14px;margin:10px 0;background:rgba(15,23,42,.55)}
.player-title {font-size:1.08rem;font-weight:800}
.player-meta {opacity:.82;font-size:.9rem;margin-top:4px}
.player-reason {margin-top:8px;line-height:1.35}
.badge {display:inline-block;padding:3px 8px;border-radius:99px;border:1px solid rgba(148,163,184,.3);font-size:.77rem;margin:4px 5px 0 0}
.source {opacity:.88;font-size:.82rem;margin-top:7px}
@media(max-width:640px){.block-container{padding-left:.75rem;padding-right:.75rem}.player-card{padding:12px}button{min-height:44px}}
</style>
""",
    unsafe_allow_html=True,
)


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


st.title("⚽ Fanta XI Assistant V3.1")
st.caption("Consenso multi-fonte + modalità API-Football ottimizzata per il piano Free.")

with st.expander("⚙️ Impostazioni", expanded=False):
    api_key = st.text_input("API-Football key", value=secret_key(), type="password")
    c1, c2 = st.columns(2)
    use_predictions = c1.toggle(
        "Matchup API",
        value=False,
        help="Disattivato di default per risparmiare quota. Le prediction richiedono una chiamata per partita.",
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
        help="Più precisa ma usa più chiamate API-Football. Lasciala spenta nel primo aggiornamento.",
    )
    clear_cache = st.button("Svuota cache API-Football")
    st.caption(
        "V3.1: le richieste vengono distanziate automaticamente quando l'API segnala il limite Free di 10/minuto. "
        "Profili, statistiche e fixture vengono messi in cache."
    )

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
    st.caption("Puoi inserire una % manuale o xG90/xA90 reali; i campi vuoti non modificano nulla.")
    over_edit = st.data_editor(overrides, use_container_width=True, hide_index=True)
    if st.button("💾 Salva override"):
        over_edit.to_csv(OVERRIDES, index=False)
        st.success("Override salvati per questa istanza dell'app.")

with tab_info:
    st.markdown(
        """
### Gerarchia dei segnali
1. **Formazione ufficiale**: prevale su tutto.
2. **Infortunio/squalifica certa**: fortissima penalità.
3. **Probabili editoriali**: Fantacalcio.it, Gazzetta e Sky Sport.
4. **News recenti**: impatto prudente per recuperi, stop e convocazioni.
5. **Statistiche API**: continuità, minuti, rating e rendimento.
6. **Matchup e bonus**: avversario, rigori, piazzati e, se attivate, prediction.

### V3.1
Per il piano gratuito API-Football l'app riduce le chiamate: ricerca giocatore e statistiche vengono ottenute insieme quando possibile, gli ID risolti vengono memorizzati, le prossime partite sono raggruppate per campionato e viene applicato un pacing automatico.

Una stima numerica interna di Sky/Gazzetta non viene presentata come una percentuale ufficiale della testata.
"""
    )
    st.link_button("Fantacalcio.it probabili", PROBABILI_URL)
    st.link_button("Gazzetta probabili", GAZZETTA_PROB_URL)
    st.link_button("Fantacalcio.it rigoristi", RIGORISTI_URL)

if clear_cache and api_key:
    APIFootball(api_key, str(BASE / ".fantacache")).cache.clear()
    st.toast("Cache API svuotata")

if "evaluations_v3" not in st.session_state:
    st.session_state.evaluations_v3 = None
if "v3_sources" not in st.session_state:
    st.session_state.v3_sources = {}
if "v3_news" not in st.session_state:
    st.session_state.v3_news = {}

with tab_xi:
    if not api_key:
        st.info("Inserisci la chiave API-Football in ⚙️ Impostazioni o nei Secrets di Streamlit.")
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
    probable_teams = probable_src.teams if probable_src and probable_src.ok else {}
    setpiece_src = fetch_set_piece_roles(names) if use_set_pieces else None
    setpiece_map = setpiece_src.roles if setpiece_src and setpiece_src.ok else {}

    ctx = []
    prog.progress(.08, text="Risoluzione giocatori API...")
    for idx, r in roster.iterrows():
        name = str(r["name"]).strip()
        role = str(r["role"]).strip().upper()
        key = norm(name)
        team_hint = probable_teams.get(key, "")
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

            sb = api.best_stat_block(row)
            if row and row.get("player"):
                resolved = row["player"].get("name") or resolved
            if not sb and team_hint:
                sb = {"team": {"name": team_hint}}
        except Exception as exc:
            err = str(exc)
            if team_hint:
                sb = {"team": {"name": team_hint}}

        ctx.append({
            "name": name,
            "role": role,
            "pid": pid,
            "resolved": resolved,
            "conf": conf,
            "row": row,
            "sb": sb,
            "err": err,
            "team_hint": team_hint,
        })
        prog.progress(.08 + .34 * (idx + 1) / len(roster), text=f"API giocatori: {idx+1}/{len(roster)}")

    team_ids = list(dict.fromkeys([
        ((p.get("sb") or {}).get("team") or {}).get("id")
        for p in ctx
        if ((p.get("sb") or {}).get("team") or {}).get("id")
    ]))

    fixtures_by_team = {}
    league_groups: dict[int, set[int]] = {}
    for p in ctx:
        sb = p.get("sb") or {}
        tid = (sb.get("team") or {}).get("id")
        lid = (sb.get("league") or {}).get("id")
        if tid and lid:
            league_groups.setdefault(int(lid), set()).add(int(tid))

    prog.progress(.43, text="Prossime partite per campionato...")
    for lid, tids in league_groups.items():
        try:
            games = api.next_fixtures_for_league(lid, season, count=max(20, len(tids) * 3))
        except Exception:
            games = []
        for fx in games:
            teams = fx.get("teams") or {}
            for side in ("home", "away"):
                tid = ((teams.get(side) or {}).get("id"))
                if tid in tids and tid not in fixtures_by_team:
                    fixtures_by_team[tid] = fx

    missing = [tid for tid in team_ids if tid not in fixtures_by_team]
    for j, tid in enumerate(missing):
        try:
            fixtures_by_team[tid] = api.next_fixture(tid)
        except Exception:
            fixtures_by_team[tid] = None
        if missing:
            prog.progress(.43 + .09 * (j + 1) / len(missing), text="Fallback prossime partite...")

    player_source_rows = []
    for p in ctx:
        sb = p.get("sb") or {}
        team_name = (sb.get("team") or {}).get("name", "") or p.get("team_hint", "")
        player_source_rows.append({"name": p["name"], "team": team_name})

    prog.progress(.54, text="Incrocio Gazzetta e Sky...")
    gazzetta_map = ms.gazzetta_signals(player_source_rows) if use_gazzetta else {}
    prog.progress(.59)
    sky_map = ms.sky_signals(player_source_rows) if use_sky else {}
    prog.progress(.64, text="Lettura news recenti...")
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
        (fx.get("fixture") or {}).get("id")
        for fx in fixtures_by_team.values()
        if fx and (fx.get("fixture") or {}).get("id")
    ]))

    try:
        inj_list = api.injuries_for_fixtures(fixture_ids)
    except Exception:
        inj_list = []
    injuries = {
        (x.get("player") or {}).get("id"): x
        for x in inj_list
        if (x.get("player") or {}).get("id")
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
        for fid in fixture_ids:
            fx = next((x for x in fixtures_by_team.values() if x and (x.get("fixture") or {}).get("id") == fid), None)
            if fx and api.is_close_to_kickoff(fx):
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
                for x in team_recent[tid]
                if (x.get("fixture") or {}).get("id")
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

    prog.progress(.80, text="Calcolo schierabilità...")
    evaluations = []
    source_debug = {}
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
        for signal in (gazzetta_map.get(key), sky_map.get(key)):
            if signal:
                ext.append(signal.to_dict())

        news_items = news_map.get(key, [])
        news_adj, news_notes = ms.news_adjustment(news_items)

        ev = evaluate_player(
            name=p["name"],
            role=p["role"],
            api_player_id=p.get("pid"),
            resolved_name=p["resolved"],
            resolve_confidence=p["conf"],
            stat_block=sb,
            fixture=fx,
            injury=injuries.get(p.get("pid")),
            official_status=official_status,
            prediction=predictions.get(fid),
            probable_pct=fantapct,
            recent_matches=recent_by_player.get(p.get("pid"), []),
            set_piece_role=setpiece_map.get(key),
            xg90=m.get("xg90"),
            xa90=m.get("xa90"),
            manual_note=m.get("note", ""),
            external_source_estimates=ext,
            news_adjustment=news_adj,
            news_notes=news_notes,
        )
        if p.get("err"):
            ev.reason += "; dati API incompleti"
        evaluations.append(ev)
        source_debug[key] = {
            "api_error": p.get("err", ""),
            "api_resolved": p.get("resolved", ""),
            "api_confidence": p.get("conf", 0),
            "fantacalcio_pct": fantapct,
            "gazzetta": gazzetta_map.get(key).to_dict() if gazzetta_map.get(key) else None,
            "sky": sky_map.get(key).to_dict() if sky_map.get(key) else None,
            "news_adjustment": news_adj,
        }
        prog.progress(.80 + .20 * (i + 1) / len(ctx))

    fantacalcio_found = sum(1 for n in names if norm(n) in probable_map)
    resolved_ok = sum(1 for p in ctx if p.get("pid") and p.get("conf", 0) >= 75)
    st.session_state.evaluations_v3 = evaluations
    st.session_state.v3_sources = {
        "api_diag": api.diagnostics(),
        "api_players_resolved": resolved_ok,
        "fantacalcio_ok": bool(probable_src and probable_src.ok),
        "fantacalcio_updated": probable_src.updated_at if probable_src else "",
        "fantacalcio_found": fantacalcio_found,
        "gazzetta_found": len(gazzetta_map),
        "sky_found": len(sky_map),
        "news_found": sum(len(x) for x in news_map.values()),
        "debug": source_debug,
    }
    st.session_state.v3_news = {
        key: [x.to_dict() for x in values] for key, values in news_map.items()
    }
    prog.empty()
    st.rerun()

evaluations = st.session_state.evaluations_v3
status = st.session_state.v3_sources
news_saved = st.session_state.v3_news

with tab_xi:
    if evaluations:
        choice = best_lineup(evaluations)
        starters = choice["players"]
        subs = bench(evaluations, starters)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Modulo", choice["formation"])
        m2.metric("Indice XI", f'{choice["total"] / 11:.0f}/100')
        total_signals = (
            status.get("fantacalcio_found", 0)
            + status.get("gazzetta_found", 0)
            + status.get("sky_found", 0)
        )
        m3.metric("Fonti probabili", f"{total_signals} segnali")
        m4.metric("News", status.get("news_found", 0))
        st.caption("V3.1 · API Free friendly · fixture raggruppate per campionato · cache automatica")

        for role, title in [("P", "PORTIERE"), ("D", "DIFENSORI"), ("C", "CENTROCAMPISTI"), ("A", "ATTACCANTI")]:
            group = sorted(
                [x for x in starters if x.role == role],
                key=lambda x: x.schierabilita,
                reverse=True,
            )
            if not group:
                continue
            st.markdown(f"### {title}")
            for p in group:
                consensus = (
                    f'<span class="badge">Consenso fonti {p.source_consensus_pct:.0f}%</span>'
                    if p.source_consensus_pct is not None
                    else ""
                )
                st.markdown(
                    f"""
<div class="player-card">
  <div class="player-title">{esc(p.label)} · {esc(p.name.title())} — {p.schierabilita:.0f}%</div>
  <div class="player-meta">{esc(p.team)} vs {esc(p.opponent)} · {esc(p.home_away)}</div>
  <div>
    <span class="badge">Titolare {p.p_starter:.0f}%</span>
    {consensus}
    <span class="badge">{p.source_count} fonti</span>
    <span class="badge">Confidenza {esc(p.confidence)}</span>
  </div>
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
        d = status.get("api_diag", {})
        st.subheader("📡 Diagnostica API-Football")
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Chiamate rete", d.get("network_calls", 0))
        a2.metric("Da cache", d.get("cache_hits", 0))
        a3.metric(
            "Quota giornaliera",
            f"{d.get('daily_remaining') or '—'}/{d.get('daily_limit') or '100'} rimaste",
        )
        a4.metric(
            "Quota minuto",
            f"{d.get('minute_remaining') or '—'}/{d.get('minute_limit') or '10'} rimaste",
        )
        if d.get("rate_limit_hits"):
            st.warning(f"Rate limit incontrato {d['rate_limit_hits']} volte: retry automatico eseguito.")
        if d.get("waited_seconds"):
            st.caption(f"Attesa automatica per rispettare i limiti: {d['waited_seconds']} s")
        if d.get("errors"):
            with st.expander(f"⚠️ Dettagli errori API ({d['errors']})"):
                for msg in d.get("last_errors", []):
                    st.code(msg)

        st.subheader("🗞️ Copertura fonti")
        f1, f2, f3, f4 = st.columns(4)
        f1.metric("Fantacalcio.it", status.get("fantacalcio_found", 0))
        f2.metric("Gazzetta", status.get("gazzetta_found", 0))
        f3.metric("Sky Sport", status.get("sky_found", 0))
        f4.metric("News", status.get("news_found", 0))
        st.caption(
            f"Giocatori API abbinati con confidenza ≥75%: {status.get('api_players_resolved', 0)}/{len(evaluations)}"
        )

        debug = status.get("debug", {})
        for p in sorted(evaluations, key=lambda x: x.schierabilita, reverse=True):
            key = norm(p.name)
            with st.expander(f"{p.name.title()} · {p.p_starter:.0f}% titolare · {p.confidence}"):
                st.write("**Consenso:**", p.source_detail or "Nessun segnale editoriale")
                info = debug.get(key, {})
                if info.get("api_resolved"):
                    st.caption(
                        f"API: {info.get('api_resolved')} · confidenza abbinamento {info.get('api_confidence', 0)}%"
                    )
                if info.get("api_error"):
                    st.error(info["api_error"])
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
            "name": "Giocatore",
            "role": "R",
            "team": "Squadra",
            "opponent": "Avversario",
            "p_starter": "% titolare",
            "source_consensus_pct": "Consenso fonti",
            "source_count": "N. fonti",
            "source_detail": "Dettaglio fonti",
            "schierabilita": "% schierabilità",
            "label": "Giudizio",
            "recent_rating": "Rating recenti",
            "threat_score": "Pericolosità",
            "fixture_score": "Matchup",
            "reason": "Motivo",
        })
        cols = [
            "Giocatore", "R", "Squadra", "Avversario", "% titolare",
            "Consenso fonti", "N. fonti", "% schierabilità", "Giudizio",
            "Rating recenti", "Pericolosità", "Matchup", "Dettaglio fonti", "Motivo",
        ]
        st.dataframe(df[[c for c in cols if c in df.columns]], hide_index=True, use_container_width=True)
    else:
        st.info("Aggiorna prima la formazione.")
