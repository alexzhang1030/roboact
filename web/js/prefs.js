/** Browser-only display preferences. Keys always use the `roboact.` prefix.
 *  Execution / safety config stays on the server.
 */
export const KEYS = {
  theme: 'roboact.theme',
  lang: 'roboact.lang',
  motion: 'roboact.motion',
  leftOpen: 'roboact.leftOpen',
  rightW: 'roboact.rightW',
  grid: 'roboact.grid',
  lastFlow: 'roboact.lastFlow',
  canvas: 'roboact.canvas',
}

export function readRaw(key) {
  try { return localStorage.getItem(key) } catch (_) { return null }
}

export function writeRaw(key, value) {
  try {
    if (value == null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch (_) {}
}

export function readChoice(key, allowed, fallback) {
  const v = readRaw(key)
  return allowed.includes(v) ? v : fallback
}

/** First-load language. One function so the rule is a one-line change.
 *  Current rule (Alex 2026-10-31): English regardless of browser language.
 */
export function detectBrowserLang(_languages) {
  return 'en'
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
