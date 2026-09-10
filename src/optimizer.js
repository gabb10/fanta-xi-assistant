export const FORMATIONS = [
  { name: '3-4-3', D: 3, C: 4, A: 3 },
  { name: '3-5-2', D: 3, C: 5, A: 2 },
  { name: '4-3-3', D: 4, C: 3, A: 3 },
  { name: '4-4-2', D: 4, C: 4, A: 2 },
  { name: '4-5-1', D: 4, C: 5, A: 1 },
  { name: '5-3-2', D: 5, C: 3, A: 2 },
  { name: '5-4-1', D: 5, C: 4, A: 1 },
]

export function playerScore(player) {
  const availability = player.status === 'out' ? 0 : player.status === 'doubt' ? 0.72 : 1
  const starter = Math.max(0, Math.min(1, player.starterProbability ?? 0.5))
  const form = player.form ?? 6
  const expected = player.expectedPoints ?? form
  return availability * (expected * 0.58 + form * 0.22 + starter * 10 * 0.20)
}

function top(players, role, count) {
  return players
    .filter((p) => p.role === role)
    .map((p) => ({ ...p, score: playerScore(p) }))
    .sort((a, b) => b.score - a.score)
    .slice(0, count)
}

export function optimizeLineup(players) {
  const goalkeepers = top(players, 'P', 1)
  if (!goalkeepers.length) return null

  const candidates = FORMATIONS.map((formation) => {
    const selected = [
      ...goalkeepers,
      ...top(players, 'D', formation.D),
      ...top(players, 'C', formation.C),
      ...top(players, 'A', formation.A),
    ]
    const valid = selected.length === 11
    return {
      formation: formation.name,
      players: selected,
      score: valid ? selected.reduce((sum, p) => sum + p.score, 0) : -Infinity,
    }
  }).filter((x) => Number.isFinite(x.score))

  candidates.sort((a, b) => b.score - a.score)
  const best = candidates[0]
  if (!best) return null

  const starters = new Set(best.players.map((p) => p.id))
  const bench = players
    .filter((p) => !starters.has(p.id))
    .map((p) => ({ ...p, score: playerScore(p) }))
    .sort((a, b) => b.score - a.score)

  return { ...best, bench }
}
