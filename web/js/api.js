function fail(r, j) {
  const err = new Error(j.error || j.message || r.statusText)
  err.code = j.error
  err.body = j
  err.status = r.status
  throw err
}

export async function get(path) {
  const r = await fetch(path)
  const j = await r.json().catch(() => ({}))
  if (!r.ok) fail(r, j)
  return j
}

export async function send(path, method, body) {
  const r = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body == null ? undefined : JSON.stringify(body),
  })
  if (r.status === 204) return {}
  const j = await r.json().catch(() => ({}))
  if (!r.ok) fail(r, j)
  return j
}

export function openEvents(runId, after, handlers) {
  const params = new URLSearchParams()
  if (after != null && after !== '') params.set('after', String(after))
  const q = params.toString() ? ('?' + params.toString()) : ''
  const es = new EventSource('/api/runs/' + encodeURIComponent(runId) + '/events' + q)
  const names = handlers || {}
  for (const [name, fn] of Object.entries(names)) {
    es.addEventListener(name, ev => {
      try { fn(JSON.parse(ev.data), ev) } catch (_) {}
    })
  }
  return es
}
