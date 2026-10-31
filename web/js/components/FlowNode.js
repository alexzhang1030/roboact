import { defineComponent, markRaw, computed, inject } from 'vue'
import { Handle, Position } from '@vue-flow/core'
import { nodeStatus } from '../status-store.js'
import { inputPorts } from '../graph-utils.js'

const WORDS = {
  idle: '空闲',
  queued: '排队',
  running: '运行中',
  paused: '已暂停',
  paused_before: '已暂停 · 执行前',
  paused_after: '已暂停 · 执行后',
  awaiting_approval: '等待确认',
  done: '完成',
  error: '出错',
  skipped: '跳过',
  stopped: '已停止',
  interrupted: '中断',
  interrupted_unknown: '中断 · 结果未知',
  cancel_rejected: '取消被拒',
  action_missing: '动作已不存在',
}

const ICONS = {
  idle: 'state-idle',
  queued: 'state-queued',
  running: 'state-running',
  paused: 'state-paused',
  paused_before: 'state-paused',
  paused_after: 'state-paused',
  awaiting_approval: 'state-awaiting',
  done: 'state-done',
  error: 'state-error',
  skipped: 'state-skipped',
  stopped: 'state-stopped',
  interrupted: 'state-interrupted',
  interrupted_unknown: 'state-unknown',
  cancel_rejected: 'state-running',
  action_missing: 'state-error',
}

// data-status / icon color kind. interrupted_unknown is never folded into error.
const KIND = {
  paused: 'paused',
  paused_before: 'paused',
  paused_after: 'paused',
  awaiting_approval: 'awaiting',
  stopped: 'stopped',
  interrupted: 'interrupted',
  interrupted_unknown: 'unknown',
  cancel_rejected: 'running',
  action_missing: 'missing',
}

export default markRaw(defineComponent({
  name: 'FlowNode',
  props: ['id', 'data'],
  components: { Handle },
  emits: ['toggle-bp'],
  setup(props) {
    const nodeTypes = inject('nodeTypes', [])
    const portSources = inject('portSources', {})
    const toggleBp = inject('toggleBp', () => {})
    const tools = inject('tools', [])
    const iconBase = inject('iconBase', '/vendor/icons')
    const spec = computed(() => (nodeTypes.value || nodeTypes).find(t => t.type === props.data.nodeType || t.type_id === props.data.nodeType))
    const ins = computed(() => inputPorts(spec.value, props.data.params))
    const outs = computed(() => (spec.value && spec.value.outputs) || [])
    const st = computed(() => {
      const raw = nodeStatus[props.id] || { status: 'idle', state: 'idle' }
      return { ...raw, status: raw.state || raw.status || 'idle' }
    })
    const kind = computed(() => KIND[st.value.status] || st.value.status || 'idle')
    const icon = computed(() => ICONS[st.value.status] || 'state-idle')
    const typeIcon = computed(() => (spec.value && spec.value.icon) || '')
    const isTimeout = computed(() => {
      const s = st.value
      if (s.status === 'interrupted_unknown') return false
      const msg = s.error_summary || s.error || ''
      return s.error_type === 'timeout' || (typeof msg === 'string' && String(msg).includes('超时'))
    })
    const word = computed(() => {
      const s = st.value
      if (s.status === 'cancel_rejected') return '取消被拒'
      if (s.status === 'action_missing') return '动作已不存在'
      if (s.status === 'running') {
        if (s.phase === 'waiting_precondition' || (s.word && String(s.word).includes('等待前提条件'))) {
          return '运行中 · 等待前提条件'
        }
        if (s.feedback) return '运行中 · ' + s.feedback
        if (s.progress != null && s.progress !== '') return '运行中 · ' + Math.round(s.progress) + '%'
        if (s.elapsed_s != null) return '运行中 · ' + Math.round(s.elapsed_s) + ' s'
        if (s.word && String(s.word).startsWith('运行中')) return s.word
        return '运行中'
      }
      if (s.pause_reason === 'restart' || s.reason === 'restart') return '已暂停 · 服务重启后'
      if (s.status === 'paused' && s.phase === 'before') return '已暂停 · 执行前'
      if (s.status === 'paused' && s.phase === 'after') return '已暂停 · 执行后'
      if (s.status === 'paused_before' && s.phase !== 'after') return s.word || '已暂停 · 执行前'
      if (s.status === 'paused_after') return s.word || '已暂停 · 执行后'
      let base = s.word || WORDS[s.status] || '空闲'
      if (s.status === 'done' && (s.side_effect_mode === 'mock' || s.mocked)) base = '完成 · 模拟'
      else if (s.status === 'done' && s.duration_ms != null) {
        const ms = s.duration_ms
        base = ms < 10 ? '完成 · ' + Math.round(ms) + ' ms' : '完成 · ' + (ms / 1000).toFixed(1) + ' s'
      }
      if (s.extra) base += s.extra
      return base
    })
    const summary = computed(() => {
      const s = st.value
      if (kind.value === 'unknown') return s.error_summary || '外部可能已写入'
      if (kind.value === 'error') return s.error_summary || s.error || (isTimeout.value ? '超时' : '')
      return ''
    })
    const write = computed(() => {
      const s = spec.value
      const params = (props.data && props.data.params) || {}
      const list = tools.value || tools || []
      if (props.data.nodeType === 'tool_call') {
        const allowed = params.allowed_tools && params.allowed_tools.length
          ? params.allowed_tools
          : [params.tool || 'clock']
        return allowed.some(name => {
          const t = list.find(x => x.name === name)
          return t && t.side_effect === 'write'
        })
      }
      if (!s) return false
      if (s.shows_write_mark != null) return !!s.shows_write_mark
      return s.side_effect === 'write' || s.side_effect === 'motion'
    })
    const rows = computed(() => Math.max(ins.value.length, outs.value.length, 1))
    const bpBefore = computed(() => (props.data.breakpoints || {}).before || {})
    const bpAfter = computed(() => (props.data.breakpoints || {}).after || {})
    const srcOf = (port) => (portSources.value || portSources)[props.id + ':' + port]
    const icoUrl = (name) => 'url("' + iconBase + '/' + name + '.svg")'
    return { Position, ins, outs, st, word, write, rows, spec, bpBefore, bpAfter, srcOf, toggleBp, kind, icon, typeIcon, iconBase, icoUrl, summary }
  },
  template: `
  <div class="fn" :data-status="kind" :data-state="st.status" :data-node-type="data.nodeType">
    <div class="bp before" :class="{ on: bpBefore.enabled, cond: bpBefore.round || (bpBefore.condition && bpBefore.condition.field) }"
         :title="bpBefore.enabled ? '执行前断点' : '点这里设执行前断点'"
         @click.stop="toggleBp(id, 'before')">
      <span class="dot"></span>
    </div>
    <div class="bp after" :class="{ on: bpAfter.enabled, cond: bpAfter.round || (bpAfter.condition && bpAfter.condition.field) }"
         :title="bpAfter.enabled ? '执行后断点' : '点这里设执行后断点'"
         @click.stop="toggleBp(id, 'after')">
      <span class="dot"></span>
    </div>
    <div class="fn-title">
      <span v-if="typeIcon" class="fn-type-ico" :style="{ '--ico': icoUrl(typeIcon) }"></span>
      <span v-if="write" class="fn-side" :style="{ '--ico': icoUrl('mark-write') }" :title="(spec && spec.side_effect === 'motion') ? '会让机器人动' : ((spec && spec.side_effect_note) || '会写外部')"></span>
      <span class="fn-name">{{ data.title || data.nodeType }}</span>
      <span class="fn-st">
        <span class="ico" :data-kind="kind" :style="{ '--ico': icoUrl(icon) }"></span>
        <span class="fn-st-word">{{ word }}</span>
      </span>
    </div>
    <div class="fn-err" v-if="summary" :class="{ amber: kind === 'unknown' }">{{ summary }}</div>
    <div class="fn-ports">
      <div class="fn-port" v-for="n in rows" :key="n">
        <template v-if="ins[n-1]">
          <Handle type="target" :position="Position.Left" :id="ins[n-1].name"
                  :class="(srcOf(ins[n-1].name) && srcOf(ins[n-1].name).kind) || ''" />
          <span class="fn-port-name">{{ ins[n-1].label || ins[n-1].name }}</span>
          <span class="src" v-if="srcOf(ins[n-1].name)">{{ srcOf(ins[n-1].name).text }}</span>
        </template>
        <span style="flex:1"></span>
        <template v-if="outs[n-1]">
          <span class="fn-port-name">{{ outs[n-1].label || outs[n-1].name }}</span>
          <Handle type="source" :position="Position.Right" :id="outs[n-1].name" />
        </template>
      </div>
    </div>
  </div>`
}))
