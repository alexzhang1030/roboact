export function createHistory(limit = 80) {
  const past = []
  const future = []
  return {
    push(snap) {
      past.push(JSON.stringify(snap))
      if (past.length > limit) past.shift()
      future.length = 0
    },
    undo(current) {
      if (!past.length) return null
      future.push(JSON.stringify(current))
      return JSON.parse(past.pop())
    },
    redo(current) {
      if (!future.length) return null
      past.push(JSON.stringify(current))
      return JSON.parse(future.pop())
    },
    canUndo() { return past.length > 0 },
    canRedo() { return future.length > 0 },
  }
}
