import React, { useEffect, useMemo, useState } from 'react'
import { optimizeLineup } from './optimizer.js'

const statusLabel = { fit: 'Disponibile', doubt: 'In dubbio', out: 'Out' }

function Player({ player, bench = false }) {
  return (
    <article className={`player ${player.status || 'fit'}`}>
      <div className="role">{player.role}</div>
      <div className="player-main">
        <strong>{player.name}</strong>
        <span>{player.team} · {statusLabel[player.status] || 'Disponibile'}</span>
      </div>
      <div className="metrics">
        <b>{Math.round((player.starterProbability ?? 0) * 100)}%</b>
        <span>{bench ? 'titolarità' : `score ${player.score?.toFixed(1)}`}</span>
      </div>
    </article>
  )
}

export default function App() {
  const [players, setPlayers] = useState([])
  const [updatedAt, setUpdatedAt] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    fetch('./data/latest.json')
      .then((r) => { if (!r.ok) throw new Error('Dati non disponibili'); return r.json() })
      .then((data) => { setPlayers(data.players || []); setUpdatedAt(data.updatedAt) })
      .catch((e) => setError(e.message))
  }, [])

  const result = useMemo(() => optimizeLineup(players), [players])

  return (
    <main className="shell">
      <header className="hero">
        <div><p className="eyebrow">FANTACALCIO · 10 SQUADRE · NO MODIFICATORE</p><h1>Fanta XI Assistant</h1></div>
        <div className="live"><i /> Dati {updatedAt ? new Date(updatedAt).toLocaleString('it-IT') : 'in caricamento'}</div>
      </header>

      {error && <div className="alert">{error}</div>}
      {!result && !error && <div className="card">Caricamento formazione…</div>}

      {result && <>
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
          <h2>Come verranno incrociate le fonti</h2>
          <p>Ogni giocatore riceverà segnali separati per titolarità, indisponibilità, forma e news. Gli adapter potranno assegnare peso diverso alle fonti e conservare data/ora dell'ultimo aggiornamento.</p>
        </section>
      </>}
    </main>
  )
}
