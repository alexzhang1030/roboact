/** Browser-only display preferences. Keys always use the `roboact.` prefix.
 *  Execution / safety config stays on the server.
 */
export const STORAGE_PREFIX = 'roboact.'

export const KEYS = {
  theme: 'roboact.theme',
  lang: 'roboact.lang',
  motion: 'roboact.motion',
  leftOpen: 'roboact.leftOpen',
  rightW: 'roboact.rightW',
  grid: 'roboact.grid',
  lastFlow: 'roboact.lastFlow',
  canvas: 'roboact.canvas',
  token: 'roboact.token',
}

function prefixed(key) {
  return typeof key === 'string' && key.startsWith(STORAGE_PREFIX)
}

export function readRaw(key) {
  if (!prefixed(key)) return null
  try { return localStorage.getItem(key) } catch (_) { return null }
}

export function writeRaw(key, value) {
  if (!prefixed(key)) return
  try {
    if (value == null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch (_) {}
}

/** Access token for a non-loopback bind. Session only (PRD v1.4.6). */
export function readSession(key) {
  if (!prefixed(key)) return null
  try { return sessionStorage.getItem(key) } catch (_) { return null }
}

export function writeSession(key, value) {
  if (!prefixed(key)) return
  try {
    if (value == null) sessionStorage.removeItem(key)
    else sessionStorage.setItem(key, value)
  } catch (_) {}
}

export function readChoice(key, allowed, fallback) {
  const v = readRaw(key)
  return allowed.includes(v) ? v : fallback
}

/** First-load language. Flip to `false` for always-English (pending Alex).
 *  This constant is the one-line change; keep `index.html` `FOLLOW_BROWSER_LANG` identical.
 */
export const FOLLOW_BROWSER_LANG = true

/** Sole first-load locale function. Head script mirrors this body. */
export function detectBrowserLang(languages) {
  if (!FOLLOW_BROWSER_LANG) return 'en'
  const list = languages || (typeof navigator !== 'undefined' ? navigator.languages : null) || []
  const first = (list && list[0]) || (typeof navigator !== 'undefined' ? navigator.language : '') || ''
  return String(first).toLowerCase().startsWith('zh') ? 'zh-CN' : 'en'
}

export function detectBrowserTheme() {
  try {
    return (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light'
  } catch (_) {
    return 'light'
  }
}

export function detectBrowserMotionReduce() {
  try {
    return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  } catch (_) {
    return false
  }
}

/** Last opened flow id. Reject junk so a bad localStorage value is ignored. */
export function readLastFlow() {
  const v = readRaw(KEYS.lastFlow)
  if (!v || v.length > 80 || /[^a-zA-Z0-9_-]/.test(v)) return null
  return v
}

/** Canvas pan/zoom. Bad or partial JSON falls back to Vue Flow defaults. */
export function readCanvas() {
  const raw = readRaw(KEYS.canvas)
  if (!raw) return null
  try {
    const v = JSON.parse(raw)
    if (!v || typeof v !== 'object') return null
    const x = Number(v.x), y = Number(v.y), zoom = Number(v.zoom)
    if (![x, y, zoom].every(Number.isFinite)) return null
    if (zoom <= 0 || zoom > 8) return null
    return { x, y, zoom }
  } catch (_) {
    return null
  }
}

export function writeCanvas(v) {
  if (!v) {
    writeRaw(KEYS.canvas, null)
    return
  }
  writeRaw(KEYS.canvas, JSON.stringify({ x: v.x, y: v.y, zoom: v.zoom }))
}
