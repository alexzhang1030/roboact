import { ref } from 'vue'
import en from '../i18n/en.js'
import zhCN from '../i18n/zh-CN.js'
import {
  KEYS, readChoice, writeRaw, detectBrowserLang, detectBrowserTheme, detectBrowserMotionReduce,
} from './prefs.js'

export const CATALOGS = { en, 'zh-CN': zhCN }

function interpolate(s, params) {
  if (!params) return s
  return String(s).replace(/\{(\w+)\}/g, (_, k) => (params[k] == null ? '' : String(params[k])))
}

export const localeMode = ref(readChoice(KEYS.lang, ['system', 'en', 'zh-CN'], 'system'))
export const locale = ref(localeMode.value === 'system' ? detectBrowserLang() : localeMode.value)

export const themeMode = ref(readChoice(KEYS.theme, ['system', 'light', 'dark'], 'system'))
export const theme = ref(themeMode.value === 'system' ? detectBrowserTheme() : themeMode.value)

export const motionMode = ref(readChoice(KEYS.motion, ['system', 'reduce', 'full'], 'system'))
export const motion = ref(
  motionMode.value === 'reduce' || (motionMode.value === 'system' && detectBrowserMotionReduce())
    ? 'reduce'
    : 'full',
)

export function applyLocale() {
  const resolved = localeMode.value === 'en' || localeMode.value === 'zh-CN'
    ? localeMode.value
    : detectBrowserLang()
  locale.value = resolved
  if (typeof document !== 'undefined') {
    document.documentElement.lang = resolved
    document.documentElement.dataset.lang = resolved
    document.documentElement.dataset.langMode = localeMode.value
  }
}

export function applyTheme() {
  const resolved = themeMode.value === 'light' || themeMode.value === 'dark'
    ? themeMode.value
    : detectBrowserTheme()
  theme.value = resolved
  if (typeof document !== 'undefined') {
    document.documentElement.dataset.theme = resolved
    document.documentElement.dataset.themeMode = themeMode.value
  }
}

export function applyMotion() {
  const reduce = motionMode.value === 'reduce'
    || (motionMode.value === 'system' && detectBrowserMotionReduce())
  motion.value = reduce ? 'reduce' : 'full'
  if (typeof document !== 'undefined') {
    document.documentElement.dataset.motion = motion.value
    document.documentElement.dataset.motionMode = motionMode.value
  }
}

export function setLocaleMode(mode) {
  localeMode.value = mode
  writeRaw(KEYS.lang, mode === 'en' || mode === 'zh-CN' ? mode : null)
  applyLocale()
}

export function setThemeMode(mode) {
  themeMode.value = mode
  writeRaw(KEYS.theme, mode)
  applyTheme()
}

export function setMotionMode(mode) {
  motionMode.value = mode
  writeRaw(KEYS.motion, mode)
  applyMotion()
}

export function t(key, params) {
  return catalogLookup(key, locale.value, params)
}

const SERVER_LABEL_KEYS = ['adapter.fake', 'adapter.generic', 'flow.demo', 'flow.untitled']

export function serverLabel(value, keys) {
  if (value == null || value === '') return ''
  const s = String(value)
  const list = keys || SERVER_LABEL_KEYS
  for (const key of list) {
    if (s === key) return t(key)
    if (s === CATALOGS.en[key] || s === CATALOGS['zh-CN'][key]) return t(key)
  }
  return s
}

const EN_COUNT_ONE = {
  'run.action_missing_count': '1 missing action',
  'run.missing_input_count': '1 node needs input',
}

export function countLabel(key, n) {
  const loc = locale.value
  const num = Number(n)
  if (loc === 'en') {
    const form = new Intl.PluralRules('en').select(num)
    if (form === 'one' && EN_COUNT_ONE[key]) return EN_COUNT_ONE[key]
  }
  return t(key, { n: num })
}

export function catalogLookup(key, loc, params) {
  const L = loc || locale.value
  const primary = CATALOGS[L] || CATALOGS.en
  let raw
  if (primary && Object.prototype.hasOwnProperty.call(primary, key)) raw = primary[key]
  else if (Object.prototype.hasOwnProperty.call(CATALOGS.en, key)) raw = CATALOGS.en[key]
  else return ''
  return interpolate(raw, params)
}

export function actionLabel(spec, loc) {
  if (!spec) return ''
  const L = loc || locale.value
  const type = spec.type || spec.type_id || spec.id
  if (type) {
    const named = catalogLookup('node.' + type, L)
    if (named) return named
  }
  const labels = spec.labels || (spec.action && spec.action.labels) || {}
  return labels[L] || labels.en || spec.name || spec.title || spec.id || spec.type || ''
}

export function portLabel(port, loc) {
  if (!port) return ''
  const named = catalogLookup('port.' + (port.name || ''), loc)
  if (named) return named
  return port.label || port.name || ''
}

export function fieldLabel(key, fallback, loc) {
  const named = catalogLookup('field.' + key, loc)
  if (named) return named
  return fallback || key
}

export function mapError(code, params) {
  if (!code) return ''
  const aliases = {
    import_failed: 'flow.import_failed',
    run_locked: 'flow.run_locked',
    param_type_mismatch: 'run.param_type_mismatch',
    param_out_of_limit: 'run.param_out_of_limit',
    motion_timeout_cap: 'run.motion_timeout_cap',
    missing_input: 'status.missing_input',
  }
  const key = aliases[code] || ('error.' + code)
  const hit = t(key, params)
  if (hit) return hit
  if (/^[a-z][a-z0-9_]*$/.test(String(code))) {
    const enHit = CATALOGS.en[key]
    return enHit ? interpolate(enHit, params) : ''
  }
  return String(code)
}

export function statusWord(st) {
  if (!st) return t('status.idle')
  const status = st.state || st.status || 'idle'
  if (status === 'cancel_rejected') return t('status.cancel_rejected')
  if (status === 'action_missing') return t('status.action_missing')
  if (status === 'missing_input') return t('status.missing_input')
  if (status === 'interrupted_unknown' || status === 'interrupted') return t('status.' + status)
  if (status === 'running') {
    if (st.phase === 'waiting_precondition') return t('status.running_wait')
    if (st.feedback) {
      const text = Object.prototype.hasOwnProperty.call(CATALOGS.en, st.feedback)
        ? t(st.feedback, st.feedback_params)
        : st.feedback
      return t('status.running_feedback', { text })
    }
    if (st.progress != null && st.progress !== '') return t('status.running_pct', { pct: Math.round(st.progress) })
    if (st.elapsed_s != null) return t('status.running_s', { s: Math.round(st.elapsed_s) })
    return t('status.running')
  }
  if (st.pause_reason === 'restart' || st.reason === 'restart') return t('status.paused_restart')
  if (status === 'paused' && st.phase === 'before') return t('status.paused_before')
  if (status === 'paused' && st.phase === 'after') return t('status.paused_after')
  if (status === 'paused_before') return t('status.paused_before')
  if (status === 'paused_after') return t('status.paused_after')
  if (status === 'awaiting_approval') return t('status.awaiting')
  if (status === 'done') {
    if (st.side_effect_mode === 'mock' || st.mocked) return t('status.done_mock')
    if (st.duration_ms != null) {
      const ms = st.duration_ms
      return ms < 10 ? t('status.done_ms', { ms: Math.round(ms) }) : t('status.done_s', { s: (ms / 1000).toFixed(1) })
    }
    return t('status.done')
  }
  const key = 'status.' + status
  const hit = t(key)
  return hit || t('status.idle')
}

export function applyStorageEvent(e) {
  if (!e || !e.key || !String(e.key).startsWith('roboact.')) return
  if (e.key === KEYS.theme) {
    const v = e.newValue
    themeMode.value = (v === 'light' || v === 'dark' || v === 'system') ? v : 'system'
    applyTheme()
  } else if (e.key === KEYS.lang) {
    const v = e.newValue
    localeMode.value = (v === 'en' || v === 'zh-CN' || v === 'system') ? v : 'system'
    applyLocale()
  } else if (e.key === KEYS.motion) {
    const v = e.newValue
    motionMode.value = (v === 'reduce' || v === 'full' || v === 'system') ? v : 'system'
    applyMotion()
  }
}

export function syncAll() {
  applyLocale()
  applyTheme()
  applyMotion()
}
