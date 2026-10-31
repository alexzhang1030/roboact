/** Browser-only display preferences. Keys always use the `roboact.` prefix.
 *  Execution / safety config stays on the server. No auth keys.
 */
export const STORAGE_PREFIX = 'roboact.'

export const KEYS = {
  theme: 'roboact.theme',
  lang: 'roboact.lang',
  motion: 'roboact.motion',
  panels: 'roboact.panels',
  grid: 'roboact.grid',
  logFilter: 'roboact.logFilter',
  viewportPrefix: 'roboact.viewport.',
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

export function readPanels() {
  const raw = readRaw(KEYS.panels)
  if (!raw) return { leftOpen: true, rightW: 360 }
  try {
    const v = JSON.parse(raw)
    if (!v || typeof v !== 'object') return { leftOpen: true, rightW: 360 }
    const n = Number(v.rightW)
    return {
      leftOpen: v.leftOpen !== false && v.leftOpen !== 0 && v.leftOpen !== '0',
      rightW: Number.isFinite(n) && n >= 320 ? n : 360,
    }
  } catch (_) {
    return { leftOpen: true, rightW: 360 }
  }
}

export function writePanels(leftOpen, rightW) {
  writeRaw(KEYS.panels, JSON.stringify({ leftOpen: !!leftOpen, rightW }))
}

function parseViewport(raw) {
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

export function viewportKey(flowId) {
  if (!flowId || /[^a-zA-Z0-9_-]/.test(flowId)) return null
  return KEYS.viewportPrefix + flowId
}

export function readViewport(flowId) {
  const key = viewportKey(flowId)
  return key ? parseViewport(readRaw(key)) : null
}

export function writeViewport(flowId, v) {
  const key = viewportKey(flowId)
  if (!key) return
  if (!v) {
    writeRaw(key, null)
    return
  }
  writeRaw(key, JSON.stringify({ x: v.x, y: v.y, zoom: v.zoom }))
}

export function readLogFilter() {
  return readChoice(KEYS.logFilter, ['all', 'error'], 'all')
}
