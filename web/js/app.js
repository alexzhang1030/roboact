import { createApp, defineComponent, ref, computed, watch, nextTick, provide, onMounted, onUnmounted } from 'vue'
import { VueFlow, useVueFlow, MarkerType } from '@vue-flow/core'
import { MiniMap } from '@vue-flow/minimap'
import FlowNode from './components/FlowNode.js'
import LoopEdge from './components/LoopEdge.js'
import { nodeStatus, resetStatus, applyNodeStates } from './status-store.js'
import { isBackEdge, typesOk, portType, toFlow, fromFlow } from './graph-utils.js'
import { get, send, openEvents, setToken, setUnauthorizedHandler } from './api.js'
import { createHistory } from './history.js'

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
      data: { nodeType: 'constant', title: '节点 ' + i, params: { value: String(i), value_type: 'string' }, breakpoints: {} },
    })
    if (i > 0) edges.push({ id: 'e' + i, source: 'n' + (i - 1), target: 'n' + i, sourceHandle: 'value', targetHandle: 'value', markerEnd: MarkerType.ArrowClosed })
    if (i % 10 === 9) edges.push({ id: 'b' + i, source: 'n' + i, target: 'n' + (i - 9), type: 'loop', sourceHandle: 'value', targetHandle: 'value', markerEnd: MarkerType.ArrowClosed })
  }
  return { nodes, edges }
}

function galleryGraph() {
  const mk = (id, x, status, title) => ({
    id, type: 'agent', position: { x, y: 80 },
    data: { nodeType: 'constant', title, params: { value: id, value_type: 'string' }, breakpoints: {} },
  })
  return {
    nodes: [mk('done', 40, 'done', '完成节点'), mk('paused', 320, 'paused', '暂停节点'), mk('error', 600, 'error', '出错节点')],
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
    const meta = ref({ id: '', name: '未命名流程', version: 1 })
    const nodes = ref([])
    const edges = ref([])
    const groups = ref([])
    const leftOpen = ref(true)
    const rightW = ref(360)
    const tab = ref('params')
    const selected = ref(null)
    const run = ref(null)
    const records = ref([])
    const runs = ref([])
    const pins = ref([])
    const conn = ref('ok')
    const theme = ref(localStorage.getItem('roboact-theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'))
    const needToken = ref(false)
    const tokenInput = ref('')
    const tokenErr = ref('')
    const showMini = ref(false)
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

    document.documentElement.dataset.theme = theme.value

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
    const nodeRecords = computed(() => records.value.filter(r => r.node_id === selected.value))
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
    const slot1Label = computed(() => {
      if (pausePending.value) return '暂停中…'
      if (isRunning.value) return '暂停'
      if (isPaused.value) return '继续'
      return '运行'
    })
    const slot1Disabled = computed(() => controlsOff.value || pausePending.value || waitingApproval.value || needsReadback.value || (adapterLost.value && adapterDown.value))
    const slot2Disabled = computed(() => controlsOff.value || !isPaused.value || waitingApproval.value || needsReadback.value || (adapterLost.value && adapterDown.value))
    const slot3Disabled = computed(() => controlsOff.value || !(isRunning.value || isPaused.value))
    const runWord = computed(() => {
      const r = run.value
      if (!r) return ''
      if (r.status === 'running') return '运行中'
      if (r.status === 'paused') {
        const why = (r.pause && r.pause.reason) || ''
        if (why === 'approval') return '等待确认'
        if (why === 'confirm_after_crash') return '已暂停 · 服务重启后'
        if (why === 'breakpoint') return '已暂停 · 断点'
        if (why === 'step_done') return '已暂停 · 单步'
        if (why === 'user') return '已暂停 · 手动'
        if (why === 'restart') return '已暂停 · 服务重启后'
        if (why === 'write_timeout') return '已暂停 · 写入超时'
        if (why === 'motion_timeout') return '已暂停 · 动作超时'
        if (why === 'adapter_lost') return '已暂停 · 机器人控制器断开'
        if (why === 'operator_lost' || why === 'ui_lost') return '已暂停 · 页面断开'
        if (why === 'storage_error') return '已暂停 · 记录写不进去'
        if (r.stop_requested) return '停止被拒'
        return '已暂停'
      }
      if (r.status === 'completed') return '已完成'
      if (r.status === 'stopped') return '已停止'
      if (r.status === 'error') return '出错'
      if (r.status === 'step_limit') return '超出步数上限'
      return ''
    })
    const runWordKind = computed(() => {
      const w = runWord.value
      if (w === '已暂停 · 机器人控制器断开' || w === '已暂停 · 记录写不进去' || w === '停止被拒') return 'red'
      if (w.startsWith('已暂停') || w === '等待确认' || w === '超出步数上限') return 'amber'
      if (w === '出错') return 'red'
      return 'muted'
    })
    const confirmTitle = computed(() => {
      const nid = run.value && run.value.pause && run.value.pause.node_id
      const n = nodes.value.find(x => x.id === nid)
      return (n && n.data && n.data.title) || nid || '节点'
    })
    const banners = computed(() => {
      const list = []
      if (storageErr.value) list.push({ id: 'storage', at: bannerClock.storage, kind: 'danger', text: '运行记录写不进去：' + storageErr.value + '。运行已暂停。', actions: 'storage' })
      if (waitingApproval.value) list.push({ id: 'approval', at: bannerClock.approval, kind: 'amber', text: '「' + confirmTitle.value + '」在等你确认。', actions: 'approval' })
      if (versionWarn.value) list.push({ id: 'version', at: bannerClock.version, kind: 'neutral', text: '画布已改，这次运行仍按 v' + run.value.flow_version + '。', actions: 'version' })
      return list.sort((a, b) => b.at - a.at).slice(0, 2)
    })
    const selectedWait = computed(() => {
      const s = selected.value && nodeStatus[selected.value]
      if (!s) return null
      if (s.phase === 'waiting_precondition' || (s.word && String(s.word).includes('等待前提条件'))) return s
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
      const s = run.value ? run.value.steps_used ?? run.value.global_step ?? run.value.step ?? 0 : 0
      const lim = run.value ? (run.value.max_steps ?? run.value.step_limit) : stepLimit.value
      if (run.value && run.value.status === 'step_limit') return '停 · 步数 ' + s + ' / 上限 ' + lim
      return '步数 ' + s + ' / 上限 ' + lim
    })
    const paletteTypes = computed(() => nodeTypesList.value.filter(t => t.palette !== false))
    const paletteGroups = computed(() => {
      const map = {}
      for (const t of paletteTypes.value) {
        const g = t.group || (t.from_adapter ? (adapter.value.name || '适配器') : '流程控制')
        ;(map[g] = map[g] || []).push(t)
      }
      return Object.entries(map).map(([name, items]) => ({ name, items }))
    })
    const paletteStatus = computed(() => {
      const a = adapter.value || {}
      if (a.status === 'loading' || !a.name) return { kind: 'loading', text: '正在读取适配器动作…' }
      if (a.status === 'disconnected' || a.connected === false) return { kind: 'disconnected', text: '未连上机器人控制器' }
      if (a.status === 'empty' || (a.actions && a.actions.length === 0 && !paletteTypes.value.some(t => t.from_adapter))) {
        return { kind: 'empty', text: '适配器没有动作' }
      }
      const when = a.fetched_at ? (' · ' + String(a.fetched_at).replace('T', ' ').slice(0, 19)) : ''
      return { kind: 'ok', text: (a.source || a.name || '') + when }
    })
    const filteredTypes = computed(() => {
      const qv = addQ.value.trim()
      const src = paletteTypes.value
      if (!qv) return src
      return src.filter(t => (t.label || t.title || '').includes(qv) || t.type.includes(qv))
    })
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
        data: { nodeType: type, title: spec.label, params: { ...(spec.default_params || {}) }, breakpoints: {} },
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
      } catch (e) { overlay.value = e.message }
    }

    function setBpRound() {
      const n = selectedNode.value
      if (!n) return
      const raw = prompt('只在第几轮停（空=每一轮）', ((n.data.breakpoints || {}).before || {}).round || '')
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
      groups.value = groups.value.concat([{ id: 'g_' + Date.now().toString(16), name: '分组', node_ids: sel.map(n => n.id) }])
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
      } catch (e) { overlay.value = e.message }
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

    async function connectWithToken() {
      const t = (tokenInput.value || '').trim()
      if (!t) { tokenErr.value = '请填写访问令牌。'; return }
      setToken(t)
      tokenErr.value = ''
      try {
        await loadAll()
        needToken.value = false
        startAdapterPoll()
      } catch (e) {
        if (e.status === 401) tokenErr.value = '令牌不对。'
        else tokenErr.value = e.message || '连不上。'
      }
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
          nodeStatus[n.id] = { status: 'action_missing', state: 'action_missing', word: '动作已不存在' }
        }
      }
      if (b.run) {
        run.value = normalizeRun(b.run)
        records.value = b.records || []
        applyNodeStates(b.run.nodes || b.run.node_status)
        applyPauseReason(b.run.pause)
        listen(run.value.run_id || run.value.id)
      }
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
          map[key] = { kind: 'pinned-unused', text: '本次未用固定值' }
        } else {
          map[key] = { kind: 'pinned', text: '已固定' }
        }
      }
      for (const rec of records.value) {
        const stale = rec.stale && (rec.stale.is_stale === undefined ? rec.stale : rec.stale.is_stale)
        const src = rec.input_source || ''
        for (const port of Object.keys(rec.input || rec.inputs || {})) {
          const key = rec.node_id + ':' + port
          if (map[key] && map[key].kind === 'pinned-unused') continue
          let kind = src, text = ''
          if (src === 'cache') { kind = stale ? 'stale' : 'cache'; text = stale ? '缓存 · 上游已改' : '缓存' + (rec.round ? ' · 第 ' + rec.round + ' 轮' : '') }
          if (src === 'pinned') { kind = 'pinned'; text = '已固定' }
          if (src === 'manual') { kind = 'manual'; text = '手填' }
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
        rejectMsg.value = e.message
        overlay.value = e.message
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
        else overlay.value = e.message
      }
    }

    function showAwaitingLine() {
      mismatch.value = '这一步在等人工确认，先通过或驳回。'
      mismatchFade.value = false
      clearTimeout(mismatchTimer)
      clearTimeout(mismatchFadeTimer)
      mismatchFadeTimer = setTimeout(() => { mismatchFade.value = true }, 3600)
      mismatchTimer = setTimeout(() => { mismatch.value = ''; mismatchFade.value = false }, 4000)
      const rid = run.value && (run.value.run_id || run.value.id)
      if (rid) refreshRecords(rid)
    }

    function showMismatch() {
      mismatch.value = '另一个页面已经继续了这次运行。'
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
      else if (ev.key === 'Escape') addMenu.value = null
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
      const text = (spec && spec.confirm_text) || '将按所选工具执行外部操作（可能写入文件）。'
      if (!window.confirm(text)) return
      doRun('single', { node_id: selected.value, side_effects: 'real', confirm: text, source_run_id: run.value && run.value.id })
    }
    async function decide(decision) {
      await cmd('approve', { decision, comment: confirmNote.value || undefined })
    }
    async function resolveMotion(resolution) {
      const rb = readback.value
      if (!rb || !rb.readback_id) {
        overlay.value = '正在读机器人状态…'
        return
      }
      await cmd('continue', { resolution, readback_id: rb.readback_id })
    }

    function toggleTheme() {
      theme.value = theme.value === 'dark' ? 'light' : 'dark'
      document.documentElement.dataset.theme = theme.value
      localStorage.setItem('roboact-theme', theme.value)
    }

    function fitGrid(el) {
      if (!el) return
      const v = vf.viewport.value
      el.style.backgroundPosition = v.x + 'px ' + v.y + 'px'
      el.style.backgroundSize = (20 * v.zoom) + 'px ' + (20 * v.zoom) + 'px'
    }
    watch(() => vf.viewport.value, () => {
      const pane = document.querySelector('.vue-flow')
      if (pane) {
        pane.style.backgroundImage = 'radial-gradient(var(--grid) 1px, transparent 1px)'
        fitGrid(pane)
      }
    }, { deep: true })

    onMounted(async () => {
      window.addEventListener('keydown', onKeyDown)
      if (PERF_N) {
        nodeTypesList.value = [{ type: 'constant', label: '常量', inputs: [], outputs: [{ name: 'value', json_type: 'any', label: '值' }], default_params: {} }]
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
        nodeTypesList.value = [{ type: 'constant', label: '常量', inputs: [], outputs: [{ name: 'value', json_type: 'any', label: '值' }], default_params: {} }]
        const g = galleryGraph()
        nodes.value = g.nodes
        edges.value = g.edges
        nodeStatus.done = { status: 'done', duration_ms: 1200 }
        nodeStatus.paused = { status: 'paused', phase: 'before' }
        nodeStatus.error = { status: 'error', error: '超时' }
        vf.onNodesInitialized(() => { window.__ready = true })
        return
      }
      setUnauthorizedHandler(() => { needToken.value = true; stopAdapterPoll() })
      try {
        await loadAll()
        needToken.value = false
        startAdapterPoll()
      } catch (e) {
        if (e.status === 401) {
          needToken.value = true
          window.__ready = true
        } else overlay.value = e.message
      }
      vf.onNodesInitialized(() => { window.__ready = true })
    })
    onUnmounted(() => { window.removeEventListener('keydown', onKeyDown); if (es) es.close(); stopBeat(); stopAdapterPoll() })

    window.__af = { nodes, edges, run, adapter, doRun, cmd, toggleBp, vf, nodeStatus, decide, pausePending, slot1Label, slot1Disabled, slot2Disabled, banners, mismatch, pauseRun, runWord, runWordKind, needToken, adapterLost, uiLost, operatorLost, rejectedActions, resolveMotion, theme, selected, tab, toggleTheme }

    return {
      nodeTypesList, tools, adapter, meta, nodes, edges, groups, leftOpen, rightW, tab, selected, selectedNode,
      run, records, runs, conn, theme, showMini, addMenu, addQ, rejectMsg, overlay, specOf,
      nodeRecords, rounds, shownRec, viewRound, runActive, waitingApproval, confirmNote, versionWarn, stepsClass, stepsText,
      filteredTypes, paletteTypes, paletteGroups, paletteStatus, groupBoxes, portSources, stepLimit, overrides, singleRound, compareA, compareB, compareOut,
      deadlineAdapterOnly, outputImages, isReal, crashRecovery, selectedSpec,
      lodLow, nodeTypes: { agent: FlowNode }, edgeTypes: { loop: LoopEdge },
      slot1Label, slot1Disabled, slot2Disabled, slot3Disabled, slot1Action, pauseRun,
      runWord, runWordKind, banners, mismatch, mismatchFade, selectedUnknown, selectedWait, writeTimeoutHint, selectedCancelRejected,
      needToken, tokenInput, tokenErr, connectWithToken, adapterLost, uiLost, operatorLost,
      rejectedActions, needsReadback, readback, readingState, resolveMotion,
      addNode, onPaletteDrag, onDrop, toggleBp, setBpRound, removeSelected, makeGroup, scheduleSave,
      onConnect, onNodeDragStop, onNodesChange, onPaneClick, onCanvasDbl,
      doRun, cmd, rerunNew, downloadFlow, uploadFlow, doCompare, onRightDown, updateParam,
      pinShown, runSingle, runFrom, runGroup, really, decide, toggleTheme, isValidConnection,
      MarkerType,
    }
  },
  template: `
  <div v-if="needToken" class="token-gate">
    <form class="token-box" @submit.prevent="connectWithToken">
      <label for="access-token">访问令牌</label>
      <input id="access-token" v-model="tokenInput" type="password" autocomplete="off" />
      <button type="submit" class="primary">连接</button>
      <p class="token-err" v-if="tokenErr">{{ tokenErr }}</p>
    </form>
  </div>
  <div v-else class="shell" :class="{ 'lod-low': lodLow }">
    <div class="top">
      <input class="name" v-model="meta.name" @change="scheduleSave(true)" />
      <span class="ver">v{{ meta.version }}</span>
      <span class="adapter-name" v-if="adapter.name">{{ adapter.name }}</span>
      <span class="real-tag" v-if="isReal">真机</span>
      <div class="ctrls">
        <div class="slots">
          <button class="primary slot-1" :disabled="slot1Disabled" @click="slot1Action">{{ slot1Label }} <span class="kbd" v-if="slot1Label==='运行' || slot1Label==='继续'">F5</span></button>
          <button class="slot-2" :disabled="slot2Disabled" @click="cmd('step')">单步 <span class="kbd" v-if="!slot2Disabled">F10</span></button>
          <button class="slot-3" :disabled="slot3Disabled" title="停在原地，电机不断电。急停请用硬件按钮。" @click="cmd('stop')">停止</button>
          <span class="run-word" :class="runWordKind">{{ runWord }}</span>
        </div>
        <div class="mismatch" :class="{ out: mismatchFade }" v-if="mismatch">{{ mismatch }}</div>
      </div>
      <div class="right">
        <span :class="stepsClass">{{ stepsText }}</span>
        <span><i class="dot-conn" :class="{off: conn!=='ok'}"></i> {{ conn==='ok' ? '已连接' : '重连中' }}</span>
        <button @click="toggleTheme">{{ theme==='dark' ? '浅色' : '深色' }}</button>
        <button @click="downloadFlow">保存文件</button>
        <label style="margin:0">打开<input type="file" accept="application/json" style="display:none" @change="uploadFlow"></label>
      </div>
    </div>
    <div class="banners">
      <div class="banner" v-for="b in banners" :key="b.id" :class="b.kind">
        <span>{{ b.text }}</span>
        <span class="spacer"></span>
        <template v-if="b.actions==='version'"><button @click="rerunNew">从新版本重跑</button></template>
        <template v-if="b.actions==='approval'">
          <button @click="decide('reject')">驳回</button>
          <button class="primary" @click="decide('approve')">通过</button>
        </template>
        <template v-if="b.actions==='storage'"><button @click="cmd('continue')">继续</button></template>
      </div>
    </div>
    <div class="banner" v-if="overlay && !banners.length">{{ overlay }}</div>
    <div class="body">
      <aside class="left" :class="{collapsed: !leftOpen}">
        <h2>节点</h2>
        <p class="pal-src" :class="paletteStatus.kind">{{ paletteStatus.text }}</p>
        <template v-if="paletteStatus.kind==='ok'">
          <div v-for="g in paletteGroups" :key="g.name" class="pal-group">
            <h3>{{ g.name }}</h3>
            <div class="nt" v-for="t in g.items" :key="t.type" draggable="true"
                 @dragstart="onPaletteDrag($event, t.type)" @dblclick="addNode(t.type)">
              <span class="ic" v-if="t.icon" :style="{ '--ico': 'url(/vendor/icons/' + t.icon + '.svg)' }"></span><span v-else class="ic">▢</span><span>{{ t.label || t.title }}</span>
            </div>
          </div>
          <div v-if="rejectedActions.length" class="pal-group">
            <h3>未生成的动作</h3>
            <p class="pal-err" v-for="r in rejectedActions" :key="r.id">{{ r.title || r.id }}：{{ r.reason }}</p>
          </div>
        </template>
        <p v-else class="pal-empty">{{ paletteStatus.kind==='loading' ? '加载中…' : paletteStatus.text }}</p>
      </aside>
      <div class="canvas" @dragover.prevent @drop="onDrop" @dblclick="onCanvasDbl">
        <button class="collapse" @click="leftOpen=!leftOpen">{{ leftOpen ? '收起' : '节点' }}</button>
        <VueFlow :nodes="nodes" :edges="edges" :node-types="nodeTypes" :edge-types="edgeTypes"
                 :min-zoom="0.05" :max-zoom="4" :snap-to-grid="true" :snap-grid="[20,20]"
                 :is-valid-connection="isValidConnection" fit-view-on-init
                 @connect="onConnect" @node-drag-stop="onNodeDragStop" @nodes-change="onNodesChange"
                 @pane-click="onPaneClick" @node-click="e => selected = e.node.id">
          <MiniMap v-if="showMini" pannable zoomable />
        </VueFlow>
        <div class="group-box" v-for="g in groupBoxes" :key="g.id"
             :style="{left: g.x+'px', top: g.y+'px', width: g.w+'px', height: g.h+'px', display:'none'}">
        </div>
        <div class="menu" v-if="addMenu" :style="{left: addMenu.x+'px', top: addMenu.y+'px'}">
          <input v-model="addQ" placeholder="搜索节点" autofocus>
          <div class="nt" v-for="t in filteredTypes" :key="t.type" @click="addNode(t.type, addMenu.flow)">{{ t.label }}</div>
        </div>
      </div>
      <aside class="right" :style="{width: rightW+'px'}">
        <div class="right-resize" @mousedown.prevent="onRightDown"></div>
        <template v-if="selectedNode">
          <div class="confirm-box" v-if="waitingApproval && run.pause && run.pause.node_id===selectedNode.id">
            <input class="note-in" v-model="confirmNote" placeholder="说明（可不填）">
            <div style="display:flex;gap:8px;margin-top:8px">
              <button @click="decide('reject')">驳回</button>
              <button class="primary" @click="decide('approve')">通过</button>
            </div>
          </div>
          <p class="idem-note" v-if="writeTimeoutHint">继续时会带同一个幂等键重试，外部服务按这个键去重。</p>
          <div class="readback" v-else-if="needsReadback">
            <p class="idem-note" v-if="readingState">正在读机器人状态…</p>
            <template v-else>
              <h3>读回的机器人状态</h3>
              <div class="kv">{{ JSON.stringify(readback && readback.state || {}, null, 2) }}</div>
              <div style="display:flex;gap:8px;margin-top:8px">
                <button class="primary" @click="resolveMotion('retry')">重做这一步</button>
                <button @click="resolveMotion('mark_done')">当作已做完</button>
              </div>
            </template>
          </div>
          <p class="idem-note" v-else-if="selectedUnknown">中断 · 结果未知。不会自动重跑；先看适配器读到的状态再确认。</p>
          <p class="deadline-note" v-if="deadlineAdapterOnly">到期只靠 adapter</p>
          <p class="cancel-note" v-if="selectedCancelRejected">取消被拒</p>
          <div class="recovery" v-if="crashRecovery && waitingApproval">
            <h3>重启后读到的状态</h3>
            <div class="kv">{{ JSON.stringify(crashRecovery, null, 2) }}</div>
          </div>
          <div class="tabs">
            <button :class="{on: tab==='in'}" @click="tab='in'">输入</button>
            <button :class="{on: tab==='out'}" @click="tab='out'">输出</button>
            <button :class="{on: tab==='params'}" @click="tab='params'">参数</button>
            <button :class="{on: tab==='rec'}" @click="tab='rec'">记录</button>
          </div>
          <div class="panel">
            <div class="round-nav" v-if="rounds.length">
              <button @click="viewRound=Math.max(0,viewRound-1)">‹</button>
              <span>第 {{ rounds[viewRound] || rounds[0] }} 轮 / 共 {{ rounds.length }} 轮</span>
              <button @click="viewRound=Math.min(rounds.length-1,viewRound+1)">›</button>
            </div>
            <template v-if="tab==='in'">
              <label>选用一次运行的输入</label>
              <select v-model="singleRound">
                <option :value="null">最近一轮</option>
                <option v-for="r in rounds" :key="r" :value="r">第 {{ r }} 轮</option>
              </select>
              <label>改值（JSON）</label>
              <textarea v-model="overrides" placeholder='{"value": 1}'></textarea>
              <div style="display:flex;gap:8px;margin-top:8px">
                <button class="primary" style="height:32px" @click="runSingle">只跑这个</button>
                <button style="height:32px" @click="pinShown">固定这份输入</button>
              </div>
              <button style="margin-top:8px" @click="runFrom">从这里重跑</button>
              <button v-if="selectedNode.data.nodeType==='tool_call' || selectedNode.data.nodeType==='model_call'" style="margin-top:8px" @click="really">真执行</button>
              <h3 style="margin-top:16px">当前输入</h3>
              <div class="kv">{{ JSON.stringify(shownRec && shownRec.inputs || {}, null, 2) }}</div>
            </template>
            <template v-if="tab==='out'">
              <div v-for="[name, img] in outputImages" :key="name" class="out-img">
                <img :src="img.data || img.url" :alt="name">
                <p class="img-cap">{{ name }} · {{ img.bytes || '?' }} B · {{ img.width }}×{{ img.height }} · {{ img.captured_at }} · 第 {{ shownRec && shownRec.round }} 轮 · 步 {{ shownRec && shownRec.step }}</p>
              </div>
              <div class="kv">{{ JSON.stringify(shownRec && shownRec.outputs || run && run.output || {}, null, 2) }}</div>
              <p v-if="shownRec && shownRec.error" style="color:var(--red)">{{ shownRec.error }}</p>
            </template>
            <template v-if="tab==='params'">
              <div v-if="specOf(selectedNode)">
                <div v-for="(prop, key) in (specOf(selectedNode).params_schema.properties || {})" :key="key">
                  <label>{{ prop.title || key }}</label>
                  <select v-if="key==='op'" :value="selectedNode.data.params[key]" @change="updateParam(key, $event.target.value)">
                    <option value="eq">等于</option><option value="ne">不等于</option>
                    <option value="gt">大于</option><option value="gte">大于等于</option>
                    <option value="lt">小于</option><option value="lte">小于等于</option>
                    <option value="contains">包含</option><option value="truthy">为真</option><option value="falsy">为假</option>
                  </select>
                  <select v-else-if="key==='tool'" :value="selectedNode.data.params[key]" @change="updateParam(key, $event.target.value)">
                    <option v-for="t in tools" :key="t.name" :value="t.name">{{ t.name }}（{{ t.side_effect }}）</option>
                  </select>
                  <select v-else-if="key==='value_type'" :value="selectedNode.data.params[key]" @change="updateParam(key, $event.target.value)">
                    <option value="string">文本</option><option value="number">数字</option><option value="boolean">布尔</option><option value="json">JSON</option>
                  </select>
                  <textarea v-else-if="prop.type==='object' || key==='data' || key==='slots' || key==='ports'"
                            :value="typeof selectedNode.data.params[key]==='string' ? selectedNode.data.params[key] : JSON.stringify(selectedNode.data.params[key] ?? (key==='data'?{}:[]), null, 2)"
                            @change="updateParam(key, key==='data' || key==='slots' || key==='ports' ? JSON.parse($event.target.value|| (key==='data'?'{}':'[]')) : $event.target.value)"></textarea>
                  <input v-else :value="selectedNode.data.params[key]" @change="updateParam(key, prop.type==='integer' ? Number($event.target.value) : $event.target.value)">
                </div>
                <label>总步数上限</label>
                <input type="number" v-model.number="stepLimit">
                <p style="color:var(--muted);font-size:12px">条件节点的「最多 N 轮」第一期不生效，待 Alex 定。</p>
                <button @click="setBpRound">断点：只在第 N 轮停</button>
              </div>
            </template>
            <template v-if="tab==='rec'">
              <p v-if="selectedWait" class="kv">已等 {{ selectedWait.wait_s ?? 0 }} s / 上限 {{ selectedWait.wait_max_s ?? '—' }} s</p>
              <div v-for="r in nodeRecords" :key="r.step" class="kv" style="margin-bottom:8px;border-bottom:1px solid var(--border);padding-bottom:8px">
                步 {{ r.step }} · 第 {{ r.round }} 轮 · {{ r.status }} · {{ r.duration_ms }} ms
                <div>入 {{ JSON.stringify(r.inputs) }}</div>
                <div>出 {{ JSON.stringify(r.outputs) }}</div>
              </div>
              <p v-if="!nodeRecords.length && !selectedWait" style="color:var(--muted)">还没有运行记录。</p>
            </template>
          </div>
        </template>
        <template v-else>
          <div class="panel">
            <h3>这次运行</h3>
            <p v-if="!run" style="color:var(--muted)">还没有运行。点顶栏「运行」或按 F5。</p>
            <template v-else>
              <p class="kv">状态 {{ run.status }} · 开始后步数 {{ run.steps_used || run.global_step || run.step || 0 }}</p>
              <p v-if="run.error" style="color:var(--red)">{{ run.error.message || run.error }}</p>
              <p v-if="run.status==='step_limit'">停在步数上限。最后一步见记录。</p>
              <h3>出错的节点</h3>
              <div class="err-list">
                <button v-for="r in records.filter(x=>x.status==='error')" :key="r.step" @click="selected=r.node_id">{{ r.node_id }} · {{ r.error }}</button>
                <p v-if="!records.some(x=>x.status==='error')" style="color:var(--muted)">无</p>
              </div>
              <h3>历史运行</h3>
              <select v-model="compareA"><option value="">对比 A</option><option v-for="r in runs" :key="r.id" :value="r.id">{{ r.id }} · {{ r.status }}</option></select>
              <select v-model="compareB"><option value="">对比 B</option><option v-for="r in runs" :key="r.id" :value="r.id">{{ r.id }} · {{ r.status }}</option></select>
              <button @click="doCompare">对比</button>
              <div v-if="compareOut" class="kv">{{ compareOut.map(x => x.key + (JSON.stringify((x.a||{}).outputs)===JSON.stringify((x.b||{}).outputs) ? ' 相同' : ' 不同')).join('\\n') }}</div>
            </template>
            <h3 style="margin-top:16px">分组</h3>
            <div v-for="g in groups" :key="g.id" style="margin-bottom:8px">
              {{ g.name }}（{{ g.node_ids.length }}）
              <button @click="runGroup(g)">跑这一组</button>
            </div>
            <p v-if="!groups.length" style="color:var(--muted)">框选后按 Ctrl+G 成组。</p>
          </div>
        </template>
      </aside>
    </div>
  </div>`
})

createApp(App).mount('#app')
