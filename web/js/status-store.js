import { reactive } from 'vue'
export const nodeStatus = reactive({})
export const edgeRound = reactive({})

export function resetStatus() {
  for (const k of Object.keys(nodeStatus)) delete nodeStatus[k]
  for (const k of Object.keys(edgeRound)) delete edgeRound[k]
}

export function applyNodeStates(map) {
  if (!map) return
  for (const [id, st] of Object.entries(map)) nodeStatus[id] = { ...(nodeStatus[id] || {}), ...st }
}
