import { createApp, defineComponent, ref, computed, watch, nextTick, provide, onMounted, onUnmounted } from 'vue'
import { VueFlow, useVueFlow, MarkerType } from '@vue-flow/core'
import { MiniMap } from '@vue-flow/minimap'
import FlowNode from './components/FlowNode.js'
import LoopEdge from './components/LoopEdge.js'
import { nodeStatus, resetStatus, applyNodeStates } from './status-store.js'
import { isBackEdge, typesOk, portType, toFlow, fromFlow } from './graph-utils.js'
import { get, send, openEvents } from './api.js'
import { createHistory } from './history.js'
import {
  t, locale, localeMode, theme, themeMode, motionMode, motion,
  setLocaleMode, setThemeMode, setMotionMode, applyTheme, applyLocale, applyMotion,
  applyStorageEvent, syncAll, actionLabel, mapError, fieldLabel, CATALOGS,
} from './i18n.js'
import { KEYS, writeRaw, readRaw, readPanels, writePanels, readViewport, writeViewport, readLogFilter } from './prefs.js'

const q = new URLSearchParams(location.search)
const PERF_N = +(q.get('n') || 0)
const GALLERY = q.get('gallery') === '1'
const CLIENT_ID = 'cl_' + Math.random().toString(16).slice(2)

function stressGraph(n) {
  const cols = 25, nodes = [], edges = []
  for (let i = 0; i < n; i++) {
    nodes.push({
      id: 'n' + i, type: 'agent',
      position: { x: (i % cols) * 260, y: Math.floor(i / cols) * 140 },
      data: { nodeType: 'constant', title: 'n' + i, params: { value: String(i), value_type: 'string' }, breakpoints: {} },
    })
    if (i > 0) edges.push({ id: 'e' + i, source: 'n' + (i - 1), target: 'n' + i, sourceHandle: 'value', targetHandle: 'value', markerEnd: MarkerType.ArrowClosed })
    if (i % 10 === 9) edges.push({ id: 'b' + i, source: 'n' + i, target: 'n' + (i - 9), type: 'loop', sourceHandle: 'value', targetHandle: 'value', markerEnd: MarkerType.ArrowClosed })
  }
  return { nodes, edges }
}

const GALLERY_STATE_DEFS = [
  { id: 'error', kind: 'error', icon: 'state-error', key: 'gallery.error' },
  { id: 'paused', kind: 'paused', icon: 'state-paused', key: 'gallery.paused' },
  { id: 'done', kind: 'done', icon: 'state-done', key: 'gallery.done' },
  { id: 'unknown', kind: 'unknown', icon: 'state-unknown', key: 'gallery.unknown' },
  { id: 'operator_lost', kind: 'paused', icon: '', key: 'gallery.operator_lost' },
  { id: 'adapter_lost', kind: 'error', icon: '', key: 'gallery.adapter_lost' },
]

function galleryGraph() {
  const mk = (id, x, title, breakpoints = {}) => ({
    id, type: 'agent', position: { x, y: 80 },
    data: { nodeType: 'constant', title, params: { value: id, value_type: 'string' }, breakpoints },
  })
  return {
    nodes: [
      mk('done', 40, ''),
      mk('paused', 300, '', { before: { enabled: true } }),
      mk('error', 560, ''),
      mk('unknown', 820, ''),
    ],
    edges: [],
  }
}

const App = defineComponent({
  components: { VueFlow, MiniMap, FlowNode },
  setup() {
    const nodeTypesList = ref([])
    const tools = ref([])
    const adapter = ref({ name: '', version: '', target: 'sim', status: 'loading', fetched_at: '', action_list_version: '' })
    const rejectedActions = ref([])
    const meta = ref({ id: '', name: '', version: 1 })
    const nodes = ref([])
    const edges = ref([])
    const groups = ref([])
    const _panels = readPanels()
    const leftOpen = ref(_panels.leftOpen)
    const rightW = ref(_panels.rightW)
    const tab = ref('params')
    const selected = ref(null)
    const run = ref(null)
    const records = ref([])
    const runs = ref([])
    const pins = ref([])
    const conn = ref('ok')
    const themeOpen = ref(false)
    const langOpen = ref(false)
    const motionOpen = ref(false)
    const showMini = ref(readRaw(KEYS.grid) !== '0')
    const savedViewport = ref(null)
    const defaultViewport = { x: 0, y: 0, zoom: 1 }
    const logFilter = ref(readLogFilter())
    let canvasReady = false
    let canvasTimer = null
    const addMenu = ref(null)
    const addQ = ref('')
    const rejectMsg = ref('')
    const overlay = ref('')
    const pausePending = ref(false)
    const storageErr = ref('')
    const mismatch = ref('')
    const mismatchFade = ref(false)
    const bannerClock = { storage: 0, approval: 0, version: 0 }
    let mismatchTimer = null
    let mismatchFadeTimer = null
    const hist = createHistory()
    const singleRound = ref(null)
    const viewRound = ref(0)
    const overrides = ref('')
    const compareA = ref('')
    const compareB = ref('')
    const compareOut = ref(null)
    const portSources = ref({})
    const stepLimit = ref(1000)
    let es = null
    let saveTimer = null
    let ignoreSave = false
    let beatTimer = null
    const UI_BEAT_MS = 2000

    syncAll()
    const themeMq = matchMedia('(prefers-color-scheme: dark)')
    const onThemeMq = () => { if (themeMode.value === 'system') applyTheme() }
    if (themeMq.addEventListener) themeMq.addEventListener('change', onThemeMq)
    else if (themeMq.addListener) themeMq.addListener(onThemeMq)
    const motionMq = matchMedia('(prefers-reduced-motion: reduce)')
    const onMotionMq = () => { if (motionMode.value === 'system') applyMotion() }
    if (motionMq.addEventListener) motionMq.addEventListener('change', onMotionMq)
    else if (motionMq.addListener) motionMq.addListener(onMotionMq)
    window.addEventListener('storage', onStorage)

    function pickTheme(mode) { setThemeMode(mode); themeOpen.value = false }
    function pickLang(mode) { setLocaleMode(mode); langOpen.value = false }
    function pickMotion(mode) { setMotionMode(mode); motionOpen.value = false }

    const vf = useVueFlow()
    provide('nodeTypes', nodeTypesList)
    provide('portSources', portSources)
    provide('toggleBp', (...a) => toggleBp(...a))
    provide('tools', tools)
    provide('iconBase', '/vendor/icons')

    const lodLow = ref(false)
    watch(() => vf.viewport.value.zoom < 0.6, v => { lodLow.value = v })

    const selectedNode = computed(() => nodes.value.find(n => n.id === selected.value) || null)
    const specOf = (n) => nodeTypesList.value.find(t => t.type === (n && n.data && n.data.nodeType))
    const nodeRecords = computed(() => {
      const list = records.value.filter(r => r.node_id === selected.value)
      if (logFilter.value === 'error') return list.filter(r => r.status === 'error')
      return list
    })
    const logRecords = computed(() => (
      logFilter.value === 'error' ? records.value.filter(r => r.status === 'error') : records.value
    ))
    const rounds = computed(() => {
      const rs = [...new Set(nodeRecords.value.map(r => r.round))]
      return rs.sort((a, b) => a - b)
    })
    const shownRec = computed(() => {
      const list = nodeRecords.value
      if (!list.length) return null
      const r = rounds.value[viewRound.value]
      return list.filter(x => x.round === r).slice(-1)[0] || list[list.length - 1]
    })
    const runActive = computed(() => run.value && ['running', 'paused'].includes(run.value.status))
    const waitingApproval = computed(() => {
      const why = run.value && run.value.pause && run.value.pause.reason
      return !!(run.value && run.value.status === 'paused' && why === 'approval')
    })
    const crashRecovery = computed(() => (run.value && run.value.pause && run.value.pause.recovery) || null)
    const confirmNote = ref('')
    const versionWarn = computed(() => runActive.value && run.value.flow_version != null && meta.value.version !== run.value.flow_version)
    const controlsOff = computed(() => conn.value !== 'ok')
    const isRunning = computed(() => !!(run.value && run.value.status === 'running'))
    const isPaused = computed(() => !!(run.value && run.value.status === 'paused'))
    const adapterLost = computed(() => !!(isPaused.value && run.value && run.value.pause && run.value.pause.reason === 'adapter_lost'))
    const operatorLost = computed(() => !!(isPaused.value && run.value && run.value.pause && run.value.pause.reason === 'operator_lost'))
    const uiLost = operatorLost
    const adapterDown = computed(() => {
      const a = adapter.value || {}
      return a.connected === false || a.status === 'disconnected'
    })
    const slot1Key = computed(() => {
      if (pausePending.value) return 'run.pausing'
      if (isRunning.value) return 'run.pause'
      if (isPaused.value) return 'run.resume'
      return 'run.start'
    })
    const slot1Label = computed(() => { void locale.value; return t(slot1Key.value) })
    const slot1Disabled = computed(() => controlsOff.value || pausePending.value || waitingApproval.value || needsReadback.value || (adapterLost.value && adapterDown.value))
    const slot2Disabled = computed(() => controlsOff.value || !isPaused.value || waitingApproval.value || needsReadback.value || (adapterLost.value && adapterDown.value))
    const slot3Disabled = computed(() => controlsOff.value || !(isRunning.value || isPaused.value))
    const runWord = computed(() => {
      void locale.value
      const r = run.value
      if (!r) return ''
      if (r.status === 'running') return t('run.word.running')
      if (r.status === 'paused') {
        const why = (r.pause && r.pause.reason) || ''
        if (why === 'approval') return t('run.word.approval')
        if (why === 'confirm_after_crash' || why === 'restart') return t('run.word.paused_restart')
        if (why === 'breakpoint') return t('run.word.paused_breakpoint')
        if (why === 'step_done') return t('run.word.paused_step')
        if (why === 'user') return t('run.word.paused_user')
        if (why === 'write_timeout') return t('run.word.paused_write_timeout')
        if (why === 'motion_timeout') return t('run.word.paused_motion_timeout')
        if (why === 'adapter_lost') return t('run.word.paused_adapter_lost')
        if (why === 'operator_lost' || why === 'ui_lost') return t('run.word.paused_operator_lost')
        if (why === 'storage_error') return t('run.word.paused_storage')
        if (r.stop_requested) return t('run.word.stop_rejected')
        return t('run.word.paused')
      }
      if (r.status === 'completed') return t('run.word.completed')
      if (r.status === 'stopped') return t('run.word.stopped')
      if (r.status === 'error') return t('run.word.error')
      if (r.status === 'step_limit') return t('run.word.step_limit')
      return ''
    })
    const runWordKind = computed(() => {
      const r = run.value
      if (!r) return 'muted'
      if (r.status === 'error') return 'red'
      if (r.status === 'step_limit') return 'amber'
      if (r.status === 'paused') {
        const why = (r.pause && r.pause.reason) || ''
        if (why === 'adapter_lost' || why === 'storage_error' || r.stop_requested) return 'red'
        return 'amber'
      }
      return 'muted'
    })
    const confirmTitle = computed(() => {
      const nid = run.value && run.value.pause && run.value.pause.node_id
      const n = nodes.value.find(x => x.id === nid)
      if (n && n.data && n.data.title) return n.data.title
      const spec = specOf(n)
      return actionLabel(spec, locale.value) || nid || t('node.fallback')
    })
    const banners = computed(() => {
      void locale.value
      const list = []
      if (storageErr.value) list.push({ id: 'storage', at: bannerClock.storage, kind: 'danger', text: t('banner.storage', { detail: storageErr.value }), actions: 'storage' })
      if (waitingApproval.value) list.push({ id: 'approval', at: bannerClock.approval, kind: 'amber', text: t('banner.approval', { name: confirmTitle.value }), actions: 'approval' })
      if (versionWarn.value) list.push({ id: 'version', at: bannerClock.version, kind: 'neutral', text: t('banner.version', { version: run.value.flow_version }), actions: 'version' })
      return list.sort((a, b) => b.at - a.at).slice(0, 2)
    })
    const selectedWait = computed(() => {
      const s = selected.value && nodeStatus[selected.value]
      if (!s) return null
      if (s.phase === 'waiting_precondition') return s
      return null
    })
    const selectedUnknown = computed(() => {
      const s = selected.value && nodeStatus[selected.value]
      return !!(s && (s.status === 'interrupted_unknown' || s.state === 'interrupted_unknown'))
    })
    const writeTimeoutHint = computed(() => !!(run.value && run.value.pause && run.value.pause.reason === 'write_timeout' && selectedUnknown.value))
    const needsReadback = computed(() => {
      const why = run.value && run.value.pause && run.value.pause.reason
      return !!(isPaused.value && ['motion_timeout', 'adapter_lost', 'restart'].includes(why) && selectedUnknown.value)
    })
    const readback = computed(() => (run.value && run.value.pause && run.value.pause.readback) || null)
    const readingState = computed(() => needsReadback.value && !readback.value)
    const selectedCancelRejected = computed(() => {
      const s = selected.value && nodeStatus[selected.value]
      return !!(s && (s.status === 'cancel_rejected' || s.state === 'cancel_rejected'))
    })
    watch(waitingApproval, v => { if (v) bannerClock.approval = Date.now() })
    watch(versionWarn, v => { if (v) bannerClock.version = Date.now() })
    const stepsClass = computed(() => (run.value && run.value.status === 'step_limit') ? 'steps limit' : 'steps')
    const stepsText = computed(() => {
      void locale.value
      const s = run.value ? run.value.steps_used ?? run.value.global_step ?? run.value.step ?? 0 : 0
      const lim = run.value ? (run.value.max_steps ?? run.value.step_limit) : stepLimit.value
      const key = (run.value && run.value.status === 'step_limit') ? 'steps.hit' : 'steps.normal'
      return t(key, { used: s, limit: lim })
    })
    const paletteTypes = computed(() => nodeTypesList.value.filter(t => t.palette !== false))
    const paletteGroups = computed(() => {
      const map = {}
      for (const t of paletteTypes.value) {
        const g = t.group || (t.from_adapter ? (adapter.value.name || adapter.value.id || 'adapter') : 'control')
        ;(map[g] = map[g] || []).push(t)
      }
      return Object.entries(map).map(([name, items]) => ({ name, items }))
    })
    const paletteStatus = computed(() => {
      void locale.value
      const a = adapter.value || {}
      if (a.status === 'loading' || !a.name) return { kind: 'loading', text: t('palette.loading') }
      if (a.status === 'disconnected' || a.connected === false) return { kind: 'disconnected', text: t('palette.disconnected') }
      if (a.status === 'empty' || (a.actions && a.actions.length === 0 && !paletteTypes.value.some(item => item.from_adapter))) {
        return { kind: 'empty', text: t('palette.empty') }
      }
      const when = a.fetched_at ? (' · ' + String(a.fetched_at).replace('T', ' ').slice(0, 19)) : ''
      return { kind: 'ok', text: (a.source || a.name || '') + when }
    })
    const typeLabel = (spec) => actionLabel(spec, locale.value)
    const filteredTypes = computed(() => {
      void locale.value
      const qv = addQ.value.trim()
      const src = paletteTypes.value
      if (!qv) return src
      return src.filter(item => (typeLabel(item) || '').includes(qv) || item.type.includes(qv))
    })
    const galleryStates = computed(() => {
      void locale.value
      return GALLERY_STATE_DEFS.map(s => ({ ...s, word: t(s.key) }))
    })
    watch([leftOpen, rightW], () => writePanels(leftOpen.value, rightW.value))
    watch(showMini, v => writeRaw(KEYS.grid, v ? '1' : '0'))
    watch(logFilter, v => writeRaw(KEYS.logFilter, v === 'error' ? 'error' : 'all'))
    const selectedSpec = computed(() => specOf(selectedNode.value))
    const deadlineAdapterOnly = computed(() => {
      const act = selectedSpec.value && selectedSpec.value.action
      return !!(act && (act.expiry === 'adapter_only' || act.deadline_enforced_by === 'adapter_only'))
    })
    const outputImages = computed(() => {
      const outs = (shownRec.value && (shownRec.value.outputs || shownRec.value.output)) || {}
      return Object.entries(outs).filter(([, v]) => v && typeof v === 'object' && (v.type === 'image' || (v.mime && String(v.mime).startsWith('image/'))))
    })
    const isReal = computed(() => adapter.value && adapter.value.target === 'real')

    function snap() {
      return { nodes: nodes.value, edges: edges.value, groups: groups.value, meta: meta.value }
    }
    function pushHist() { hist.push(snap()) }
    function applySnap(s) {
      if (!s) return
      ignoreSave = true
      nodes.value = s.nodes
      edges.value = s.edges
      groups.value = s.groups
      meta.value = s.meta
      nextTick(() => { ignoreSave = false; scheduleSave(true) })
    }

    function markLoopEdges() {
      for (const e of edges.value) e.type = isBackEdge(edges.value, e) ? 'loop' : 'default'
    }

    function onConnect(c) {
      const src = nodes.value.find(n => n.id === c.source)
      const dst = nodes.value.find(n => n.id === c.target)
      if (!typesOk(portType(nodeTypesList.value, src, c.sourceHandle, 'out'), portType(nodeTypesList.value, dst, c.targetHandle, 'in'))) return
      pushHist()
      vf.addEdges([{ ...c, markerEnd: MarkerType.ArrowClosed, type: 'default' }])
      nextTick(() => { markLoopEdges(); scheduleSave(true) })
    }
    function onNodeDragStop() { scheduleSave(false) }
    function onNodesChange(cs) {
      window.__dim = (window.__dim || 0) + cs.filter(c => c.type === 'dimensions').length
      const sel = cs.find(c => c.type === 'select' && c.selected)
      if (sel) selected.value = sel.id
    }
    function onPaneClick() { selected.value = null; addMenu.value = null }
    function onCanvasDbl(ev) {
      if (ev.target && ev.target.closest && ev.target.closest('.vue-flow__node')) return
      addMenu.value = { x: ev.clientX, y: ev.clientY, flow: vf.screenToFlowCoordinate({ x: ev.clientX, y: ev.clientY }) }
      addQ.value = ''
    }

    function isValidConnection(c) {
      const src = nodes.value.find(n => n.id === c.source)
      const dst = nodes.value.find(n => n.id === c.target)
      if (!src || !dst) return false
      return typesOk(portType(nodeTypesList.value, src, c.sourceHandle, 'out'), portType(nodeTypesList.value, dst, c.targetHandle, 'in'))
    }

    function addNode(type, pos) {
      const spec = nodeTypesList.value.find(t => t.type === type)
      if (!spec) return
      pushHist()
      const id = 'n_' + Math.random().toString(16).slice(2, 10)
      const p = pos || { x: 200 + Math.random() * 80, y: 160 + Math.random() * 80 }
      vf.addNodes([{
        id, type: 'agent', position: { x: Math.round(p.x / 20) * 20, y: Math.round(p.y / 20) * 20 },
        data: { nodeType: type, title: '', params: { ...(spec.default_params || {}) }, breakpoints: {} },
      }])
      addMenu.value = null
      scheduleSave(true)
    }

    function onPaletteDrag(ev, type) {
      ev.dataTransfer.setData('text/plain', type)
    }
    function onDrop(ev) {
      const type = ev.dataTransfer.getData('text/plain')
      if (!type) return
      const pos = vf.screenToFlowCoordinate({ x: ev.clientX, y: ev.clientY })
      addNode(type, pos)
    }

    async function toggleBp(id, phase) {
      const n = nodes.value.find(x => x.id === id)
      if (!n) return
      pushHist()
      const bps = { ...(n.data.breakpoints || {}) }
      const cur = { ...(bps[phase] || {}) }
      cur.enabled = !cur.enabled
      bps[phase] = cur
      n.data = { ...n.data, breakpoints: bps }
      try {
        if (cur.enabled) {
          await send('/api/flows/' + meta.value.id + '/breakpoints/' + id + '/' + phase, 'PUT', {
            mode: cur.round ? 'round' : 'every', round: cur.round || null, enabled: true,
          })
        } else {
          await send('/api/flows/' + meta.value.id + '/breakpoints/' + id + '/' + phase, 'DELETE')
        }
      } catch (e) { overlay.value = mapError(e.code) || e.message }
    }

    function setBpRound() {
      const n = selectedNode.value
      if (!n) return
      const raw = prompt(t('params.bp_prompt'), ((n.data.breakpoints || {}).before || {}).round || '')
      const bps = { ...(n.data.breakpoints || {}) }
      const cur = { ...(bps.before || { enabled: true }) }
      cur.enabled = true
      cur.round = raw ? Number(raw) : null
      bps.before = cur
      n.data = { ...n.data, breakpoints: bps }
      scheduleSave(true)
    }

    function removeSelected() {
      const sel = vf.getSelectedNodes.value
      const se = vf.getSelectedEdges.value
      if (!sel.length && !se.length) return
      pushHist()
      const ids = new Set(sel.map(n => n.id))
      nodes.value = nodes.value.filter(n => !ids.has(n.id))
      edges.value = edges.value.filter(e => !ids.has(e.source) && !ids.has(e.target) && !se.some(x => x.id === e.id))
      scheduleSave(true)
    }

    function makeGroup() {
      const sel = vf.getSelectedNodes.value
      if (sel.length < 2) return
      pushHist()
      groups.value = groups.value.concat([{ id: 'g_' + Date.now().toString(16), name: t('group.default'), node_ids: sel.map(n => n.id) }])
      scheduleSave(true)
    }

    const groupBoxes = computed(() => groups.value.map(g => {
      const ns = nodes.value.filter(n => g.node_ids.includes(n.id))
      if (!ns.length) return null
      const xs = ns.map(n => n.position.x), ys = ns.map(n => n.position.y)
      return { ...g, x: Math.min(...xs) - 16, y: Math.min(...ys) - 36, w: Math.max(...xs) - Math.min(...xs) + 272, h: Math.max(...ys) - Math.min(...ys) + 140 }
    }).filter(Boolean))

    async function scheduleSave(bump) {
      if (ignoreSave || PERF_N || GALLERY) return
      clearTimeout(saveTimer)
      saveTimer = setTimeout(() => saveFlow(bump), 280)
    }

    async function saveFlow(bump) {
      if (!meta.value.id) return
      const body = { ...toFlow(meta.value, nodes.value, edges.value, groups.value), bump: !!bump }
      body.name = meta.value.name
      try {
        const r = await send('/api/flows/' + meta.value.id, 'PUT', body)
        meta.value = { ...meta.value, version: r.flow.version, name: r.flow.name }
      } catch (e) { overlay.value = mapError(e.code) || e.message }
    }

    let adapterTimer = null
    function startAdapterPoll() {
      if (adapterTimer) return
      adapterTimer = setInterval(async () => {
        try { adapter.value = await get('/api/adapter') } catch (_) {}
      }, 1500)
    }
    function stopAdapterPoll() {
      if (adapterTimer) { clearInterval(adapterTimer); adapterTimer = null }
    }

    async function loadAll() {
      const b = await get('/api/bootstrap')
      nodeTypesList.value = (b.node_types || []).map(t => ({ ...t, type: t.type || t.type_id, label: t.label || t.title }))
      tools.value = b.tools || []
      adapter.value = b.adapter || { name: '', status: 'disconnected', target: 'sim' }
      rejectedActions.value = b.rejected_actions || (b.adapter && b.adapter.rejected_actions) || []
      stepLimit.value = b.step_limit_default || 1000
      const converted = fromFlow(b.flow, nodeTypesList.value)
      meta.value = {
        id: converted.id, name: converted.name, version: converted.version,
        adapter_id: converted.adapter_id || (b.adapter && b.adapter.id),
        action_list_version: converted.action_list_version || (b.adapter && b.adapter.action_list_version),
      }
      nodes.value = converted.nodes
      for (const bp of (b.breakpoints || [])) {
        const n = nodes.value.find(x => x.id === bp.node_id)
        if (!n) continue
        const when = bp.when || bp['when']
        n.data.breakpoints = { ...(n.data.breakpoints || {}), [when]: bp }
      }
      edges.value = converted.edges
      groups.value = converted.groups
      runs.value = b.runs || []
      pins.value = b.pins || []
      for (const n of nodes.value) {
        if (!nodeTypesList.value.find(t => t.type === n.data.nodeType)) {
          nodeStatus[n.id] = { status: 'action_missing', state: 'action_missing' }
        }
      }
      if (b.run) {
        run.value = normalizeRun(b.run)
        records.value = b.records || []
        applyNodeStates(b.run.nodes || b.run.node_status)
        applyPauseReason(b.run.pause)
        listen(run.value.run_id || run.value.id)
      }
      savedViewport.value = (!PERF_N && !GALLERY) ? readViewport(meta.value.id) : null
    }

    function normalizeRun(r) {
      if (!r) return r
      return { ...r, id: r.run_id || r.id, run_id: r.run_id || r.id }
    }

    function applyNodesEvent(ev) {
      const map = {}
      const list = Array.isArray(ev.nodes) ? ev.nodes : Object.entries(ev.nodes || {}).map(([id, st]) => ({ node_id: id, ...st }))
      for (const n of list) map[n.node_id] = n
      applyNodeStates(map)
      if (run.value) {
        run.value = {
          ...run.value,
          steps_used: ev.steps_used ?? run.value.steps_used,
          max_steps: ev.max_steps ?? run.value.max_steps,
          global_step: ev.steps_used ?? run.value.global_step,
        }
      }
      for (const e of edges.value) e.animated = (ev.edges_active || []).includes(e.id)
    }

    function stopBeat() {
      if (beatTimer) { clearInterval(beatTimer); beatTimer = null }
    }

    function startBeat(runId) {
      stopBeat()
      if (!runId) return
      const beat = () => {
        send('/api/runs/' + runId + '/ui-beat', 'POST', { client_id: CLIENT_ID }).catch(() => {})
      }
      beat()
      beatTimer = setInterval(beat, UI_BEAT_MS)
    }

    function listen(runId) {
      if (es) { es.close(); es = null }
      conn.value = 'ok'
      startBeat(runId)
      es = openEvents(runId, null, {
        snapshot: (ev) => {
          conn.value = 'ok'
          run.value = normalizeRun(ev)
          applyNodeStates(ev.nodes)
          applyPauseReason(ev.pause)
        },
        nodes: (ev) => { conn.value = 'ok'; applyNodesEvent(ev) },
        paused: (ev) => {
          conn.value = 'ok'
          pausePending.value = false
          if (run.value) run.value = { ...run.value, status: 'paused', pause: ev, paused_node: ev.node_id, paused_phase: ev.when }
          applyPauseReason(ev)
          if (window.__afPauseTimes) window.__afPauseTimes.push({ at: performance.now(), server_mono: ev.paused_at_mono })
          refreshRecords(runId)
        },
        resumed: () => {
          storageErr.value = ''
          pausePending.value = false
          if (run.value) run.value = { ...run.value, status: 'running', pause: null }
        },
        run_finished: (ev) => {
          storageErr.value = ''
          pausePending.value = false
          if (run.value) run.value = { ...run.value, status: ev.status, steps_used: ev.steps_used, error: ev.error, pause: null }
          refreshRecords(runId)
        },
        version_drift: (ev) => { if (run.value) run.value = { ...run.value, latest_flow_version: ev.latest_version } },
        storage_error: (ev) => {
          storageErr.value = ev.message || ''
          bannerClock.storage = Date.now()
        },
      })
      es.onerror = () => { conn.value = 'retry' }
      es.onopen = () => { conn.value = 'ok' }
    }

    async function refreshRecords(runId) {
      try {
        const r = await get('/api/runs/' + runId)
        records.value = r.records || []
        if (r.run) run.value = { ...run.value, ...r.run }
        rebuildSources()
      } catch (_) {}
    }

    function applyPauseReason(pause) {
      if (pause && pause.reason === 'restart' && pause.node_id) {
        applyNodeStates({ [pause.node_id]: { pause_reason: 'restart' } })
      }
    }

    function rebuildSources() {
      const map = {}
      const unused = new Set((run.value && run.value.unused_pins || []).map(p => p.node_id + ':' + p.port))
      for (const pin of pins.value) {
        const key = pin.node_id + ':' + pin.port
        if (run.value && run.value.kind === 'full' && unused.has(key)) {
          map[key] = { kind: 'pinned-unused', text: t('src.pinned_unused') }
        } else {
          map[key] = { kind: 'pinned', text: t('src.pinned') }
        }
      }
      for (const rec of records.value) {
        const stale = rec.stale && (rec.stale.is_stale === undefined ? rec.stale : rec.stale.is_stale)
        const src = rec.input_source || ''
        for (const port of Object.keys(rec.input || rec.inputs || {})) {
          const key = rec.node_id + ':' + port
          if (map[key] && map[key].kind === 'pinned-unused') continue
          let kind = src, text = ''
          if (src === 'cache') { kind = stale ? 'stale' : 'cache'; text = stale ? t('src.stale') : (rec.round ? t('src.cache_round', { round: rec.round }) : t('src.cache')) }
          if (src === 'pinned') { kind = 'pinned'; text = t('src.pinned') }
          if (src === 'manual') { kind = 'manual'; text = t('src.manual') }
          if (kind) map[key] = { kind, text, changed: stale && rec.stale && rec.stale.changed_nodes }
        }
      }
      portSources.value = map
    }

    async function doRun(mode, extra) {
      rejectMsg.value = ''
      overlay.value = ''
      await saveFlow(false)
      try {
        let r
        if (mode === 'single') {
          r = await send('/api/single-runs/node', 'POST', {
            flow_id: meta.value.id, flow_version: meta.value.version,
            node_id: extra.node_id, client_id: CLIENT_ID,
            input: extra.source_run_id ? { kind: 'record', run_id: extra.source_run_id, step: extra.step, round: extra.source_round } : { kind: 'default', round: extra.source_round },
            side_effects: extra.side_effects || 'default',
            confirm: extra.confirm,
          })
        } else if (mode === 'group') {
          r = await send('/api/single-runs/group', 'POST', {
            flow_id: meta.value.id, flow_version: meta.value.version,
            node_ids: extra.node_ids, client_id: CLIENT_ID,
            source_run_id: extra.source_run_id, side_effects: extra.side_effects || 'default',
          })
        } else if (mode === 'from') {
          r = await send('/api/runs/' + extra.source_run_id + '/rerun-from', 'POST', {
            step: extra.step, client_id: CLIENT_ID, side_effects: extra.side_effects || 'default',
          })
        } else {
          r = await send('/api/runs', 'POST', {
            flow_id: meta.value.id, flow_version: meta.value.version,
            max_steps: stepLimit.value, client_id: CLIENT_ID,
          })
        }
        resetStatus()
        const rid = r.run_id || (r.run && (r.run.run_id || r.run.id))
        const snap = r.run || await get('/api/runs/' + rid)
        run.value = normalizeRun(snap)
        applyNodeStates(snap.nodes || snap.node_status)
        applyPauseReason(snap.pause)
        records.value = []
        listen(rid)
      } catch (e) {
        rejectMsg.value = mapError(e.code) || e.message
        overlay.value = rejectMsg.value
      }
    }

    async function cmd(action, body) {
      if (!run.value) return
      const rid = run.value.run_id || run.value.id
      const pause_id = run.value.pause && run.value.pause.pause_id
      try {
        const r = await send('/api/runs/' + rid + '/' + action, 'POST', {
          pause_id, client_id: CLIENT_ID, ...(body || {}),
        })
        if (r.run) run.value = normalizeRun(r.run)
        else if (r.status) run.value = { ...run.value, status: r.status }
        if (action === 'continue' || action === 'approve') storageErr.value = ''
      } catch (e) {
        if (e.code === 'pause_mismatch') showMismatch()
        else if (e.code === 'awaiting_approval') showAwaitingLine()
        else overlay.value = mapError(e.code) || e.message
      }
    }

    function showAwaitingLine() {
      mismatch.value = t('mismatch.approval')
      mismatchFade.value = false
      clearTimeout(mismatchTimer)
      clearTimeout(mismatchFadeTimer)
      mismatchFadeTimer = setTimeout(() => { mismatchFade.value = true }, 3600)
      mismatchTimer = setTimeout(() => { mismatch.value = ''; mismatchFade.value = false }, 4000)
      const rid = run.value && (run.value.run_id || run.value.id)
      if (rid) refreshRecords(rid)
    }

    function showMismatch() {
      mismatch.value = t('mismatch.other_page')
      mismatchFade.value = false
      clearTimeout(mismatchTimer)
      clearTimeout(mismatchFadeTimer)
      mismatchFadeTimer = setTimeout(() => { mismatchFade.value = true }, 3600)
      mismatchTimer = setTimeout(() => { mismatch.value = ''; mismatchFade.value = false }, 4000)
      const rid = run.value && (run.value.run_id || run.value.id)
      if (rid) refreshRecords(rid)
    }

    function slot1Action() {
      if (slot1Disabled.value) return
      if (isRunning.value) { pauseRun(); return }
      if (isPaused.value) {
        cmd('continue', overrides.value ? { edits: { inputs: JSON.parse(overrides.value || '{}') } } : {})
        return
      }
      doRun('all')
    }

    async function pauseRun() {
      if (!isRunning.value || pausePending.value || controlsOff.value) return
      pausePending.value = true
      await cmd('pause')
    }

    async function rerunNew() {
      await doRun('all')
    }

    function downloadFlow() {
      const blob = new Blob([JSON.stringify(toFlow(meta.value, nodes.value, edges.value, groups.value), null, 2)], { type: 'application/json' })
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = (meta.value.name || 'flow') + '.json'
      a.click()
    }
    function uploadFlow(ev) {
      const f = ev.target.files && ev.target.files[0]
      if (!f) return
      const rd = new FileReader()
      rd.onload = async () => {
        const body = JSON.parse(rd.result)
        const r = await send('/api/flows/' + meta.value.id, 'PUT', { ...body, bump: true })
        await loadAll()
        meta.value.version = r.flow.version
      }
      rd.readAsText(f)
    }

    async function doCompare() {
      if (!compareA.value || !compareB.value) return
      const a = await get('/api/runs/' + compareA.value)
      const b = await get('/api/runs/' + compareB.value)
      const map = {}
      for (const r of a.records) map[r.node_id + ':' + r.round] = { a: r }
      for (const r of b.records) {
        const k = r.node_id + ':' + r.round
        map[k] = map[k] || {}
        map[k].b = r
      }
      compareOut.value = Object.entries(map).map(([k, v]) => ({ key: k, a: v.a, b: v.b }))
    }

    function onKeyDown(ev) {
      const tag = (ev.target && ev.target.tagName) || ''
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag) && ev.key !== 'Escape') return
      if (ev.key === 'F9') { ev.preventDefault(); if (selected.value) toggleBp(selected.value, 'before') }
      else if (ev.key === 'F5' && ev.shiftKey) { ev.preventDefault(); if (!slot3Disabled.value) cmd('stop') }
      else if (ev.key === 'F5') {
        ev.preventDefault()
        if (slot1Disabled.value) return
        if (isPaused.value && !waitingApproval.value) cmd('continue')
        else if (!isRunning.value && !isPaused.value) doRun('all')
      }
      else if (ev.key === 'F6') { ev.preventDefault(); pauseRun() }
      else if (ev.key === 'F10') { ev.preventDefault(); if (!slot2Disabled.value) cmd('step') }
      else if (ev.key === 'Delete' || ev.key === 'Backspace') { ev.preventDefault(); removeSelected() }
      else if ((ev.ctrlKey || ev.metaKey) && ev.key === 'z') { ev.preventDefault(); applySnap(hist.undo(snap())) }
      else if ((ev.ctrlKey || ev.metaKey) && (ev.key === 'y' || (ev.shiftKey && ev.key === 'z'))) { ev.preventDefault(); applySnap(hist.redo(snap())) }
      else if ((ev.ctrlKey || ev.metaKey) && ev.key === 'g') { ev.preventDefault(); makeGroup() }
      else if (ev.key === 'm' || ev.key === 'M') { showMini.value = !showMini.value }
      else if (ev.key === 'Escape') { addMenu.value = null; themeOpen.value = false; langOpen.value = false; motionOpen.value = false }
    }

    function onRightDown(ev) {
      const startX = ev.clientX, startW = rightW.value
      const move = (e) => { rightW.value = Math.max(320, startW - (e.clientX - startX)) }
      const up = () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up) }
      window.addEventListener('mousemove', move)
      window.addEventListener('mouseup', up)
    }

    function updateParam(key, val) {
      const n = selectedNode.value
      if (!n) return
      pushHist()
      n.data = { ...n.data, params: { ...n.data.params, [key]: val } }
      scheduleSave(true)
    }

    async function pinShown() {
      if (!selectedNode.value || !shownRec.value) return
      const n = selectedNode.value
      const spec = specOf(n)
      const ports = (spec && spec.inputs) || []
      for (const p of ports) {
        await send('/api/flows/' + meta.value.id + '/pins/' + n.id + '/' + p.name, 'PUT', {
          value: (shownRec.value.inputs || shownRec.value.input || {})[p.name],
          from: { run_id: shownRec.value.run_id, step: shownRec.value.step },
        })
      }
      pins.value = (await get('/api/flows/' + meta.value.id)).pins || pins.value
      rebuildSources()
    }

    function runSingle() {
      if (!selected.value) return
      const rec = shownRec.value
      doRun('single', { node_id: selected.value, source_run_id: rec && rec.run_id, step: rec && rec.step, source_round: singleRound.value })
    }
    function runFrom() {
      if (!selected.value || !shownRec.value) return
      doRun('from', { node_id: selected.value, source_run_id: shownRec.value.run_id || (run.value && run.value.id), step: shownRec.value.step })
    }
    function runGroup(g) {
      doRun('group', { node_ids: g.node_ids, source_run_id: run.value && run.value.id })
    }
    async function really() {
      if (!selected.value) return
      const spec = specOf(selectedNode.value)
      const text = (spec && spec.confirm_text) || t('confirm.external')
      if (!window.confirm(text)) return
      doRun('single', { node_id: selected.value, side_effects: 'real', confirm: text, source_run_id: run.value && run.value.id })
    }
    async function decide(decision) {
      await cmd('approve', { decision, comment: confirmNote.value || undefined })
    }
    async function resolveMotion(resolution) {
      const rb = readback.value
      if (!rb || !rb.readback_id) {
        overlay.value = t('readback.reading')
        return
      }
      await cmd('continue', { resolution, readback_id: rb.readback_id })
    }

    function toggleTheme() {
      setThemeMode(theme.value === 'dark' ? 'light' : 'dark')
    }

    function fitGrid(el) {
      if (!el) return
      const v = vf.viewport.value
      el.style.backgroundPosition = v.x + 'px ' + v.y + 'px'
      el.style.backgroundSize = (20 * v.zoom) + 'px ' + (20 * v.zoom) + 'px'
    }
    function applyGrid() {
      const pane = document.querySelector('.vue-flow')
      if (!pane) return
      if (showMini.value) {
        pane.style.backgroundImage = 'radial-gradient(var(--grid) 1px, transparent 1px)'
        fitGrid(pane)
      } else {
        pane.style.backgroundImage = 'none'
      }
    }
    function persistViewportNow() {
      if (PERF_N || GALLERY || !canvasReady) return
      const v = vf.viewport.value
      if (!v || !meta.value.id) return
      writeViewport(meta.value.id, { x: v.x, y: v.y, zoom: v.zoom })
    }
    function persistViewport() {
      if (PERF_N || GALLERY || !canvasReady) return
      clearTimeout(canvasTimer)
      canvasTimer = setTimeout(persistViewportNow, 200)
    }
    function restoreCanvas() {
      const saved = savedViewport.value || readViewport(meta.value && meta.value.id)
      if (saved) vf.setViewport(saved)
      else if (vf.fitView) vf.fitView()
    }
    watch(() => vf.viewport.value, () => {
      applyGrid()
      persistViewport()
    }, { deep: true })
    watch(showMini, () => applyGrid())

    function onDocClick(ev) {
      const tWrap = ev.target && ev.target.closest && ev.target.closest('.theme-wrap')
      const lWrap = ev.target && ev.target.closest && ev.target.closest('.lang-wrap')
      const mWrap = ev.target && ev.target.closest && ev.target.closest('.motion-wrap')
      if (!tWrap) themeOpen.value = false
      if (!lWrap) langOpen.value = false
      if (!mWrap) motionOpen.value = false
    }
    function onStorage(e) {
      applyStorageEvent(e)
      if (!e || !e.key || !String(e.key).startsWith('roboact.')) return
      if (e.key === KEYS.panels) {
        const p = readPanels()
        leftOpen.value = p.leftOpen
        rightW.value = p.rightW
      } else if (e.key === KEYS.grid) {
        showMini.value = e.newValue !== '0'
      } else if (e.key === KEYS.logFilter) {
        logFilter.value = e.newValue === 'error' ? 'error' : 'all'
      } else if (e.key && e.key.startsWith(KEYS.viewportPrefix) && e.newValue) {
        const v = readViewport(meta.value && meta.value.id)
        if (v && e.key === KEYS.viewportPrefix + meta.value.id) vf.setViewport(v)
      }
    }
    function groupTitle(name) {
      if (name === 'control') return t('palette.control')
      return name
    }
    function displayType(spec) { return typeLabel(spec) }

    onMounted(async () => {
      window.addEventListener('keydown', onKeyDown)
      document.addEventListener('click', onDocClick)
      if (PERF_N) {
        nodeTypesList.value = [{ type: 'constant', name: 'constant', labels: { en: CATALOGS.en['perf.constant'], 'zh-CN': CATALOGS['zh-CN']['perf.constant'] }, inputs: [], outputs: [{ name: 'value', json_type: 'any', label: 'value' }], default_params: {} }]
        const g = stressGraph(PERF_N)
        nodes.value = g.nodes
        edges.value = g.edges
        vf.onNodesInitialized(async () => {
          window.__ready = true
          await new Promise(r => setTimeout(r, 2000))
          const frames = [], longTasks = [], T0 = performance.now()
          let phase = 'idle'
          new PerformanceObserver(l => l.getEntries().forEach(e => longTasks.push({ at: Math.round(e.startTime - T0), ms: Math.round(e.duration), phase }))).observe({ type: 'longtask' })
          const STATUSES = ['idle', 'running', 'done', 'error', 'paused']
          let tick = 0, iv = null
          const setAll = () => {
            tick++
            const pane = document.querySelector('.vue-flow')
            if (pane) pane.dataset.flood = STATUSES[tick % 5]
          }
          const runPh = (ms, pan, ticks) => new Promise(res => {
            if (ticks) iv = setInterval(setAll, 100)
            const t0 = performance.now(); let last = t0
            function f(now) {
              frames.push({ phase, d: now - last }); last = now
              if (pan) { const t = (now - T0) / 1000; vf.setViewport({ x: -400 + Math.sin(t * 1.3) * 300, y: -200 + Math.cos(t) * 200, zoom: 0.45 + 0.35 * (1 + Math.sin(t * 0.9)) / 2 }) }
              if (now - t0 < ms) requestAnimationFrame(f); else { clearInterval(iv); res() }
            }
            requestAnimationFrame(f)
          })
          phase = 'pan'; await runPh(2000, true, false)
          phase = 'ticks'; await runPh(2000, false, true)
          phase = 'both'; await runPh(3000, true, true)
          const stat = ph => { const s = frames.filter(x => x.phase === ph).slice(1).map(x => x.d).sort((a, b) => a - b); const p = q => +s[Math.min(s.length - 1, Math.floor(q * s.length))].toFixed(1); return { frames: s.length, medianMs: p(0.5), p95Ms: p(0.95), maxMs: +s[s.length - 1].toFixed(1) } }
          window.__result = { n: PERF_N, pan: stat('pan'), ticks: stat('ticks'), both: stat('both'), longTasks, statusTicks: tick, dim: window.__dim || 0, domNodes: document.querySelectorAll('.vue-flow__node').length }
        })
        return
      }
      if (GALLERY) {
        nodeTypesList.value = [{ type: 'constant', name: 'constant', labels: { en: CATALOGS.en['perf.constant'], 'zh-CN': CATALOGS['zh-CN']['perf.constant'] }, inputs: [], outputs: [{ name: 'value', json_type: 'any', label: 'value' }], default_params: {} }]
        const g = galleryGraph()
        nodes.value = g.nodes
        edges.value = g.edges
        nodeStatus.done = { status: 'done', duration_ms: 1200 }
        nodeStatus.paused = { status: 'paused', phase: 'before' }
        nodeStatus.error = { status: 'error', error_type: 'timeout', error: 'timeout' }
        nodeStatus.unknown = { status: 'interrupted_unknown', error_type: 'may_have_written' }
        vf.onNodesInitialized(() => { window.__ready = true })
        return
      }
      try {
        await loadAll()
        startAdapterPoll()
      } catch (e) {
        overlay.value = mapError(e.code) || e.message
      }
      vf.onNodesInitialized(() => {
        restoreCanvas()
        nextTick(() => {
          requestAnimationFrame(() => {
            canvasReady = true
            applyGrid()
            window.__ready = true
          })
        })
      })
    })
    onUnmounted(() => {
      window.removeEventListener('keydown', onKeyDown)
      document.removeEventListener('click', onDocClick)
      if (es) es.close()
      stopBeat()
      stopAdapterPoll()
      if (themeMq.removeEventListener) themeMq.removeEventListener('change', onThemeMq)
      else if (themeMq.removeListener) themeMq.removeListener(onThemeMq)
      if (motionMq.removeEventListener) motionMq.removeEventListener('change', onMotionMq)
      else if (motionMq.removeListener) motionMq.removeListener(onMotionMq)
      window.removeEventListener('storage', onStorage)
    })

    window.__af = {
      nodes, edges, run, adapter, doRun, cmd, toggleBp, vf, nodeStatus, decide, pausePending,
      slot1Label, slot1Disabled, slot2Disabled, banners, mismatch, pauseRun, runWord, runWordKind,
      adapterLost, uiLost, operatorLost, rejectedActions, resolveMotion, theme, themeMode, setThemeMode,
      applyTheme, selected, tab, toggleTheme, locale, localeMode, setLocaleMode, motion, motionMode,
      setMotionMode, t, leftOpen, rightW, showMini, meta, logFilter,
    }

    return {
      nodeTypesList, tools, adapter, meta, nodes, edges, groups, leftOpen, rightW, tab, selected, selectedNode,
      run, records, runs, conn, theme, themeMode, themeOpen, setThemeMode, pickTheme, langOpen, locale, localeMode, pickLang,
      motion, motionMode, motionOpen, pickMotion, savedViewport, defaultViewport, persistViewportNow, logFilter,
      showMini, addMenu, addQ, rejectMsg, overlay, specOf, t, displayType, fieldLabel, groupTitle, slot1Key,
      nodeRecords, logRecords, rounds, shownRec, viewRound, runActive, waitingApproval, confirmNote, versionWarn, stepsClass, stepsText,
      filteredTypes, paletteTypes, paletteGroups, paletteStatus, groupBoxes, portSources, stepLimit, overrides, singleRound, compareA, compareB, compareOut,
      deadlineAdapterOnly, outputImages, isReal, crashRecovery, selectedSpec,
      lodLow, nodeTypes: { agent: FlowNode }, edgeTypes: { loop: LoopEdge },
      slot1Label, slot1Disabled, slot2Disabled, slot3Disabled, slot1Action, pauseRun,
      runWord, runWordKind, banners, mismatch, mismatchFade, selectedUnknown, selectedWait, writeTimeoutHint, selectedCancelRejected,
      adapterLost, uiLost, operatorLost,
      rejectedActions, needsReadback, readback, readingState, resolveMotion,
      isGallery: GALLERY, galleryStates,
      addNode, onPaletteDrag, onDrop, toggleBp, setBpRound, removeSelected, makeGroup, scheduleSave,
      onConnect, onNodeDragStop, onNodesChange, onPaneClick, onCanvasDbl,
      doRun, cmd, rerunNew, downloadFlow, uploadFlow, doCompare, onRightDown, updateParam,
      pinShown, runSingle, runFrom, runGroup, really, decide, toggleTheme, isValidConnection,
      MarkerType,
    }
  },
  template: `
  <div class="shell" :class="{ 'lod-low': lodLow }">
    <div class="top">
      <input class="name" v-model="meta.name" @change="scheduleSave(true)" />
      <span class="ver">v{{ meta.version }}</span>
      <span class="adapter-name" v-if="adapter.name">{{ adapter.name }}</span>
      <span class="real-tag" v-if="isReal">{{ t('real_tag') }}</span>
      <div class="ctrls">
        <div class="slots">
          <button class="primary slot-1" :disabled="slot1Disabled" @click="slot1Action">{{ slot1Label }} <span class="kbd" v-if="slot1Key==='run.start' || slot1Key==='run.resume'">F5</span></button>
          <button class="slot-2" :disabled="slot2Disabled" @click="cmd('step')">{{ t('run.step') }} <span class="kbd" v-if="!slot2Disabled">F10</span></button>
          <button class="slot-3" :disabled="slot3Disabled" :title="t('run.stop_title')" @click="cmd('stop')">{{ t('run.stop') }}</button>
          <span class="run-word" :class="runWordKind">{{ runWord }}</span>
        </div>
        <div class="mismatch" :class="{ out: mismatchFade }" v-if="mismatch">{{ mismatch }}</div>
      </div>
      <div class="right">
        <span :class="stepsClass">{{ stepsText }}</span>
        <span><i class="dot-conn" :class="{off: conn!=='ok'}"></i> {{ conn==='ok' ? t('conn.ok') : t('conn.retry') }}</span>
        <button @click="downloadFlow">{{ t('file.save') }}</button>
        <label class="file-open">{{ t('file.open') }}<input type="file" accept="application/json" style="display:none" @change="uploadFlow"></label>
        <div class="pref-wrap lang-wrap">
          <button type="button" class="lang-btn" :title="t('lang.title')" aria-haspopup="menu" :aria-expanded="langOpen ? 'true' : 'false'" data-testid="lang-btn" @click="langOpen = !langOpen">{{ locale === 'zh-CN' ? 'ZH' : 'EN' }}</button>
          <div class="menu theme-menu" v-if="langOpen" role="menu">
            <button type="button" role="menuitem" data-lang-mode="system" :class="{ on: localeMode==='system' }" @click="pickLang('system')">{{ t('lang.follow') }}</button>
            <button type="button" role="menuitem" data-lang-mode="en" :class="{ on: localeMode==='en' }" @click="pickLang('en')">{{ t('lang.en') }}</button>
            <button type="button" role="menuitem" data-lang-mode="zh-CN" :class="{ on: localeMode==='zh-CN' }" @click="pickLang('zh-CN')">{{ t('lang.zh') }}</button>
          </div>
        </div>
        <div class="pref-wrap motion-wrap">
          <button type="button" class="motion-btn" :title="t('motion.title')" aria-haspopup="menu" :aria-expanded="motionOpen ? 'true' : 'false'" data-testid="motion-btn" @click="motionOpen = !motionOpen">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
              <path d="M2 11c1.5-3 3-3 4 0s2.5 3 4 0 2.5-3 4 0"/>
            </svg>
          </button>
          <div class="menu theme-menu" v-if="motionOpen" role="menu">
            <button type="button" role="menuitem" data-motion-mode="system" :class="{ on: motionMode==='system' }" @click="pickMotion('system')">{{ t('motion.follow') }}</button>
            <button type="button" role="menuitem" data-motion-mode="reduce" :class="{ on: motionMode==='reduce' }" @click="pickMotion('reduce')">{{ t('motion.reduce') }}</button>
            <button type="button" role="menuitem" data-motion-mode="full" :class="{ on: motionMode==='full' }" @click="pickMotion('full')">{{ t('motion.full') }}</button>
          </div>
        </div>
        <div class="theme-wrap">
          <button type="button" class="theme-btn" :title="t('theme.title')" aria-haspopup="menu" :aria-expanded="themeOpen ? 'true' : 'false'" data-testid="theme-btn" @click="themeOpen = !themeOpen">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
              <circle cx="8" cy="8" r="5.5"/>
              <path d="M8 2.5v11a5.5 5.5 0 0 0 0-11z" fill="currentColor"/>
            </svg>
          </button>
          <div class="menu theme-menu" v-if="themeOpen" role="menu">
            <button type="button" role="menuitem" data-theme-mode="system" :class="{ on: themeMode==='system' }" @click="pickTheme('system')">{{ t('theme.follow') }}</button>
            <button type="button" role="menuitem" data-theme-mode="light" :class="{ on: themeMode==='light' }" @click="pickTheme('light')">{{ t('theme.light') }}</button>
            <button type="button" role="menuitem" data-theme-mode="dark" :class="{ on: themeMode==='dark' }" @click="pickTheme('dark')">{{ t('theme.dark') }}</button>
          </div>
        </div>
      </div>
    </div>
    <div v-if="isGallery" class="gallery-states" data-testid="prd195-states">
      <span class="gstate" v-for="s in galleryStates" :key="s.id" :data-state="s.id" :data-kind="s.kind">
        <span v-if="s.icon" class="ico" :data-kind="s.kind" :style="{ '--ico': 'url(/vendor/icons/' + s.icon + '.svg)' }"></span>
        <span class="gstate-word">{{ s.word }}</span>
      </span>
    </div>
    <div class="banners">
      <div class="banner" v-for="b in banners" :key="b.id" :class="b.kind">
        <span>{{ b.text }}</span>
        <span class="spacer"></span>
        <template v-if="b.actions==='version'"><button @click="rerunNew">{{ t('banner.rerun') }}</button></template>
        <template v-if="b.actions==='approval'">
          <button @click="decide('reject')">{{ t('banner.reject') }}</button>
          <button class="primary" @click="decide('approve')">{{ t('banner.approve') }}</button>
        </template>
        <template v-if="b.actions==='storage'"><button @click="cmd('continue')">{{ t('banner.continue') }}</button></template>
      </div>
    </div>
    <div class="banner" v-if="overlay && !banners.length">{{ overlay }}</div>
    <div class="body">
      <aside class="left" :class="{collapsed: !leftOpen}">
        <h2>{{ t('palette.title') }}</h2>
        <p class="pal-src" :class="paletteStatus.kind">{{ paletteStatus.text }}</p>
        <template v-if="paletteStatus.kind==='ok'">
          <div v-for="g in paletteGroups" :key="g.name" class="pal-group">
            <h3>{{ groupTitle(g.name) }}</h3>
            <div class="nt" v-for="item in g.items" :key="item.type" draggable="true"
                 @dragstart="onPaletteDrag($event, item.type)" @dblclick="addNode(item.type)">
              <span class="ic" v-if="item.icon" :style="{ '--ico': 'url(/vendor/icons/' + item.icon + '.svg)' }"></span><span v-else class="ic">▢</span><span>{{ displayType(item) }}</span>
            </div>
          </div>
          <div v-if="rejectedActions.length" class="pal-group">
            <h3>{{ t('palette.rejected') }}</h3>
            <p class="pal-err" v-for="r in rejectedActions" :key="r.id">{{ r.id }}: {{ r.reason }}</p>
          </div>
        </template>
        <p v-else class="pal-empty">{{ paletteStatus.text }}</p>
      </aside>
      <div class="canvas" @dragover.prevent @drop="onDrop" @dblclick="onCanvasDbl">
        <button class="collapse" @click="leftOpen=!leftOpen">{{ leftOpen ? t('palette.collapse') : t('palette.expand') }}</button>
        <VueFlow :nodes="nodes" :edges="edges" :node-types="nodeTypes" :edge-types="edgeTypes"
                 :min-zoom="0.05" :max-zoom="4" :snap-to-grid="true" :snap-grid="[20,20]"
                 :default-viewport="savedViewport || defaultViewport"
                 :fit-view-on-init="!savedViewport"
                 :is-valid-connection="isValidConnection"
                 @connect="onConnect" @node-drag-stop="onNodeDragStop" @nodes-change="onNodesChange"
                 @pane-click="onPaneClick" @node-click="e => selected = e.node.id"
                 @move-end="persistViewportNow">
          <MiniMap v-if="showMini" pannable zoomable />
        </VueFlow>
        <div class="group-box" v-for="g in groupBoxes" :key="g.id"
             :style="{left: g.x+'px', top: g.y+'px', width: g.w+'px', height: g.h+'px', display:'none'}">
        </div>
        <div class="menu" v-if="addMenu" :style="{left: addMenu.x+'px', top: addMenu.y+'px'}">
          <input v-model="addQ" :placeholder="t('search_nodes')" autofocus>
          <div class="nt" v-for="item in filteredTypes" :key="item.type" @click="addNode(item.type, addMenu.flow)">{{ displayType(item) }}</div>
        </div>
      </div>
      <aside class="right" :style="{width: rightW+'px'}">
        <div class="right-resize" @mousedown.prevent="onRightDown"></div>
        <template v-if="selectedNode">
          <div class="confirm-box" v-if="waitingApproval && run.pause && run.pause.node_id===selectedNode.id">
            <input class="note-in" v-model="confirmNote" :placeholder="t('note.placeholder')">
            <div style="display:flex;gap:8px;margin-top:8px">
              <button @click="decide('reject')">{{ t('banner.reject') }}</button>
              <button class="primary" @click="decide('approve')">{{ t('banner.approve') }}</button>
            </div>
          </div>
          <p class="idem-note" v-if="writeTimeoutHint">{{ t('idem.note') }}</p>
          <div class="readback" v-else-if="needsReadback">
            <p class="idem-note" v-if="readingState">{{ t('readback.reading') }}</p>
            <template v-else>
              <h3>{{ t('readback.title') }}</h3>
              <div class="kv">{{ JSON.stringify(readback && readback.state || {}, null, 2) }}</div>
              <div style="display:flex;gap:8px;margin-top:8px">
                <button class="primary" @click="resolveMotion('retry')">{{ t('readback.retry') }}</button>
                <button @click="resolveMotion('mark_done')">{{ t('readback.done') }}</button>
              </div>
            </template>
          </div>
          <p class="idem-note" v-else-if="selectedUnknown">{{ t('unknown.hint') }}</p>
          <p class="deadline-note" v-if="deadlineAdapterOnly">{{ t('deadline.adapter') }}</p>
          <p class="cancel-note" v-if="selectedCancelRejected">{{ t('cancel.rejected') }}</p>
          <div class="recovery" v-if="crashRecovery && waitingApproval">
            <h3>{{ t('recovery.title') }}</h3>
            <div class="kv">{{ JSON.stringify(crashRecovery, null, 2) }}</div>
          </div>
          <div class="tabs">
            <button :class="{on: tab==='in'}" @click="tab='in'">{{ t('tab.in') }}</button>
            <button :class="{on: tab==='out'}" @click="tab='out'">{{ t('tab.out') }}</button>
            <button :class="{on: tab==='params'}" @click="tab='params'">{{ t('tab.params') }}</button>
            <button :class="{on: tab==='rec'}" @click="tab='rec'">{{ t('tab.rec') }}</button>
          </div>
          <div class="panel">
            <div class="round-nav" v-if="rounds.length">
              <button @click="viewRound=Math.max(0,viewRound-1)">‹</button>
              <span>{{ t('round.nav', { round: rounds[viewRound] || rounds[0], total: rounds.length }) }}</span>
              <button @click="viewRound=Math.min(rounds.length-1,viewRound+1)">›</button>
            </div>
            <template v-if="tab==='in'">
              <label>{{ t('in.pick') }}</label>
              <select v-model="singleRound">
                <option :value="null">{{ t('round.latest') }}</option>
                <option v-for="r in rounds" :key="r" :value="r">{{ t('round.option', { round: r }) }}</option>
              </select>
              <label>{{ t('in.override') }}</label>
              <textarea v-model="overrides" placeholder='{"value": 1}'></textarea>
              <div style="display:flex;gap:8px;margin-top:8px">
                <button class="primary" style="height:32px" @click="runSingle">{{ t('in.run_this') }}</button>
                <button style="height:32px" @click="pinShown">{{ t('in.pin') }}</button>
              </div>
              <button style="margin-top:8px" @click="runFrom">{{ t('in.rerun_from') }}</button>
              <button v-if="selectedNode.data.nodeType==='tool_call' || selectedNode.data.nodeType==='model_call'" style="margin-top:8px" @click="really">{{ t('in.really') }}</button>
              <h3 style="margin-top:16px">{{ t('in.current') }}</h3>
              <div class="kv">{{ JSON.stringify(shownRec && shownRec.inputs || {}, null, 2) }}</div>
            </template>
            <template v-if="tab==='out'">
              <div v-for="[name, img] in outputImages" :key="name" class="out-img">
                <img :src="img.data || img.url" :alt="name">
                <p class="img-cap">{{ t('out.cap', { name: name, bytes: img.bytes || '?', w: img.width, h: img.height, at: img.captured_at, round: shownRec && shownRec.round, step: shownRec && shownRec.step }) }}</p>
              </div>
              <div class="kv">{{ JSON.stringify(shownRec && shownRec.outputs || run && run.output || {}, null, 2) }}</div>
              <p v-if="shownRec && shownRec.error" style="color:var(--red)">{{ shownRec.error }}</p>
            </template>
            <template v-if="tab==='params'">
              <div v-if="specOf(selectedNode)">
                <div v-for="(prop, key) in (specOf(selectedNode).params_schema.properties || {})" :key="key">
                  <label>{{ fieldLabel(key, prop.title) }}</label>
                  <select v-if="key==='op'" :value="selectedNode.data.params[key]" @change="updateParam(key, $event.target.value)">
                    <option value="eq">{{ t('params.op.eq') }}</option><option value="ne">{{ t('params.op.ne') }}</option>
                    <option value="gt">{{ t('params.op.gt') }}</option><option value="gte">{{ t('params.op.gte') }}</option>
                    <option value="lt">{{ t('params.op.lt') }}</option><option value="lte">{{ t('params.op.lte') }}</option>
                    <option value="contains">{{ t('params.op.contains') }}</option><option value="truthy">{{ t('params.op.truthy') }}</option><option value="falsy">{{ t('params.op.falsy') }}</option>
                  </select>
                  <select v-else-if="key==='tool'" :value="selectedNode.data.params[key]" @change="updateParam(key, $event.target.value)">
                    <option v-for="tool in tools" :key="tool.name" :value="tool.name">{{ tool.name }} ({{ tool.side_effect }})</option>
                  </select>
                  <select v-else-if="key==='value_type'" :value="selectedNode.data.params[key]" @change="updateParam(key, $event.target.value)">
                    <option value="string">{{ t('params.type.string') }}</option><option value="number">{{ t('params.type.number') }}</option><option value="boolean">{{ t('params.type.boolean') }}</option><option value="json">{{ t('params.type.json') }}</option>
                  </select>
                  <textarea v-else-if="prop.type==='object' || key==='data' || key==='slots' || key==='ports'"
                            :value="typeof selectedNode.data.params[key]==='string' ? selectedNode.data.params[key] : JSON.stringify(selectedNode.data.params[key] ?? (key==='data'?{}:[]), null, 2)"
                            @change="updateParam(key, key==='data' || key==='slots' || key==='ports' ? JSON.parse($event.target.value|| (key==='data'?'{}':'[]')) : $event.target.value)"></textarea>
                  <input v-else :value="selectedNode.data.params[key]" @change="updateParam(key, prop.type==='integer' ? Number($event.target.value) : $event.target.value)">
                </div>
                <label>{{ t('params.step_limit') }}</label>
                <input type="number" v-model.number="stepLimit">
                <p style="color:var(--muted);font-size:12px">{{ t('params.loop_note') }}</p>
                <button @click="setBpRound">{{ t('params.bp_round') }}</button>
              </div>
            </template>
            <template v-if="tab==='rec'">
              <label class="log-filter">{{ t('log.filter') }}
                <select v-model="logFilter" data-testid="log-filter">
                  <option value="all">{{ t('log.all') }}</option>
                  <option value="error">{{ t('log.error') }}</option>
                </select>
              </label>
              <p v-if="selectedWait" class="kv">{{ t('rec.waited', { waited: selectedWait.wait_s ?? 0, limit: selectedWait.wait_max_s ?? '—' }) }}</p>
              <div v-for="r in nodeRecords" :key="r.step" class="kv" style="margin-bottom:8px;border-bottom:1px solid var(--border);padding-bottom:8px">
                {{ t('rec.line', { step: r.step, round: r.round, status: r.status, ms: r.duration_ms }) }}
                <div>{{ t('rec.in') }} {{ JSON.stringify(r.inputs) }}</div>
                <div>{{ t('rec.out') }} {{ JSON.stringify(r.outputs) }}</div>
              </div>
              <p v-if="!nodeRecords.length && !selectedWait" style="color:var(--muted)">{{ t('rec.empty') }}</p>
            </template>
          </div>
        </template>
        <template v-else>
          <div class="panel">
            <h3>{{ t('run.this') }}</h3>
            <p v-if="!run" style="color:var(--muted)">{{ t('run.empty') }}</p>
            <template v-else>
              <p class="kv">{{ t('run.kv', { status: run.status, steps: run.steps_used || run.global_step || run.step || 0 }) }}</p>
              <p v-if="run.error" style="color:var(--red)">{{ run.error.type || run.error.message || run.error }}</p>
              <p v-if="run.status==='step_limit'">{{ t('run.step_limit_note') }}</p>
              <label class="log-filter">{{ t('log.filter') }}
                <select v-model="logFilter" data-testid="log-filter-run">
                  <option value="all">{{ t('log.all') }}</option>
                  <option value="error">{{ t('log.error') }}</option>
                </select>
              </label>
              <h3>{{ t('run.errors') }}</h3>
              <div class="err-list">
                <button v-for="r in logRecords.filter(x=>x.status==='error')" :key="r.step" @click="selected=r.node_id">{{ r.node_id }} · {{ (r.error && (r.error.type || r.error.message)) || r.status }}</button>
                <p v-if="!logRecords.some(x=>x.status==='error')" style="color:var(--muted)">{{ t('run.none') }}</p>
              </div>
              <h3>{{ t('run.history') }}</h3>
              <select v-model="compareA"><option value="">{{ t('run.compare_a') }}</option><option v-for="r in runs" :key="r.id" :value="r.id">{{ r.id }} · {{ r.status }}</option></select>
              <select v-model="compareB"><option value="">{{ t('run.compare_b') }}</option><option v-for="r in runs" :key="r.id" :value="r.id">{{ r.id }} · {{ r.status }}</option></select>
              <button @click="doCompare">{{ t('run.compare') }}</button>
              <div v-if="compareOut" class="kv">{{ compareOut.map(x => x.key + (JSON.stringify((x.a||{}).outputs)===JSON.stringify((x.b||{}).outputs) ? (' ' + t('run.same')) : (' ' + t('run.diff')))).join('\\n') }}</div>
            </template>
            <h3 style="margin-top:16px">{{ t('groups.title') }}</h3>
            <div v-for="g in groups" :key="g.id" style="margin-bottom:8px">
              {{ g.name }} ({{ g.node_ids.length }})
              <button @click="runGroup(g)">{{ t('groups.run') }}</button>
            </div>
            <p v-if="!groups.length" style="color:var(--muted)">{{ t('groups.empty') }}</p>
          </div>
        </template>
      </aside>
    </div>
  </div>`
})

createApp(App).mount('#app')
