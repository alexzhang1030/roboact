const TOKEN_KEY = 'roboact-access-token'

let unauthorizedHandler = null

export function getToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY) || ''
  } catch (_) {
    return ''
  }
}

export function setToken(token) {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token)
    else sessionStorage.removeItem(TOKEN_KEY)
  } catch (_) {}
}

export function setUnauthorizedHandler(fn) {
  unauthorizedHandler = fn
}

function authHeaders(extra) {
  const h = { ...(extra || {}) }
  const t = getToken()
  if (t) h['X-Roboact-Token'] = t
  return h
}

function fail(r, j) {
  if (r.status === 401 && unauthorizedHandler) unauthorizedHandler()
  const err = new Error(j.message || j.error || r.statusText)
  err.code = j.error
  err.body = j
  err.status = r.status
  throw err
}

export async function get(path) {
  const r = await fetch(path, { headers: authHeaders() })
  const j = await r.json().catch(() => ({}))
  if (!r.ok) fail(r, j)
  return j
}

export async function send(path, method, body) {
  const r = await fetch(path, {
    method,
    headers: authHeaders({ 'Content-Type': 'application/json' }),
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
  const t = getToken()
  if (t) params.set('token', t)
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
