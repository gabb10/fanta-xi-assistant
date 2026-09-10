import React, { useEffect, useMemo, useState } from 'react'
import { optimizeLineup } from './optimizer.js'

const statusLabel = {
  fit: 'Disponibile',
  doubt: 'In dubbio',
  out: 'Out',
  unknown: 'Da aggiornare',
}

const roleLabel = {
  P: 'Portieri',
  D: 'Difensori',
  C: 'Centrocampisti',
  A: 'Attaccanti',
}

function Player({ player, bench = false, rosterOnly = false }) {
  return (
    <article className={`player ${player.status || 'unknown'}`}>
      <div className="role">{player.role}</div>
      <div className="player-main">
        <strong>{player.name}</strong>
        <span>{player.team || 'Squadra da verificare'} · {statusLabel[player.status] || 'Da aggiornare'}</span>
      </div>
      <div className="metrics">
        {rosterOnly ? (
          <>
            <b>—</b>
            <span>dati live</span>
          </>
        ) : (
          <>
            <b>{Math.round((player.starterProbability ?? 0) * 100)}%</b>
            <span>{bench ? 'titolarità' : `score ${player.score?.toFixed(1)}`}</span>
          </>
        )}
      </div>
    </article>
  )
}

export default function App() {
  const [players, setPlayers] = useState([])
  const [updatedAt, setUpdatedAt] = useState(null)
  const [dataStatus, setDataStatus] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    fetch('./data/latest.json')
      .then((r) => { if (!r.ok) throw new Error('Dati non disponibili'); return r.json() })
      .then((data) => {
        setPlayers(data.players || [])
        setUpdatedAt(data.updatedAt)
        setDataStatus(data.dataStatus || 'live')
      })
      .catch((e) => setError(e.message))
  }, [])

  const result = useMemo(() => optimizeLineup(players), [players])
  const rosterOnly = dataStatus === 'roster-only'

  const groupedRoster = useMemo(() => {
    return ['P', 'D', 'C', 'A'].map((role) => ({
      role,
      players: players.filter((p) => p.role === role),
    }))
  }, [players])

  return (
    <main className="shell">
      <header className="hero">
        <div><p className="eyebrow">FANTACALCIO · 10 SQUADRE · NO MODIFICATORE</p><h1>Fanta XI Assistant</h1></div>
        <div className="live"><i /> Dati {updatedAt ? new Date(updatedAt).toLocaleString('it-IT') : 'in caricamento'}</div>
      </header>

      {error && <div className="alert">{error}</div>}

      {rosterOnly && !error && <>
        <section className="summary card">
          <div><span>Rosa caricata</span><strong>{players.length}/25</strong></div>
          <div><span>Modalità</span><strong>Rosa</strong></div>
          <div><span>Dati live</span><strong>In attivazione</strong></div>
        </section>

        <section className="card sources">
          <h2>Rosa importata correttamente</h2>
          <p>Per evitare consigli inventati, la formazione automatica resta sospesa finché non vengono collegati dati reali su titolarità, indisponibilità, forma, avversario e news. I nomi e i ruoli qui sotto sono già quelli della tua rosa ufficiale.</p>
        </section>

        {groupedRoster.map((group) => (
          <section key={group.role}>
            <div className="section-title"><h2>{roleLabel[group.role]}</h2><span>{group.players.length} giocatori</span></div>
            <div className="list">{group.players.map((p) => <Player key={p.id} player={p} rosterOnly />)}</div>
          </section>
        ))}
      </>}

      {!rosterOnly && !result && !error && <div className="card">Caricamento formazione…</div>}

      {!rosterOnly && result && <>
        <section className="summary card">
          <div><span>Modulo consigliato</span><strong>{result.formation}</strong></div>
          <div><span>Punteggio XI</span><strong>{result.score.toFixed(1)}</strong></div>
          <div><span>Giocatori analizzati</span><strong>{players.length}</strong></div>
        </section>

        <section>
          <div className="section-title"><h2>XI consigliato</h2><span>ordinato per ruolo</span></div>
          <div className="list">{result.players.map((p) => <Player key={p.id} player={p} />)}</div>
        </section>

        <section>
          <div className="section-title"><h2>Panchina</h2><span>priorità suggerita</span></div>
          <div className="list">{result.bench.map((p) => <Player key={p.id} player={p} bench />)}</div>
        </section>

        <section className="card sources">
          <h2>Come vengono incrociate le fonti</h2>
          <p>Ogni giocatore riceve segnali separati per titolarità, indisponibilità, forma e news. Gli adapter possono assegnare peso diverso alle fonti e conservare data e ora dell'ultimo aggiornamento.</p>
        </section>
      </>}
    </main>
  )
}
