import { defineComponent, markRaw, computed, inject } from 'vue'
import { Handle, Position } from '@vue-flow/core'
import { nodeStatus } from '../status-store.js'
import { inputPorts } from '../graph-utils.js'
import { t, locale, actionLabel, statusWord, mapError, portLabel } from '../i18n.js'

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
  action_missing: 'state-unknown',
  missing_input: 'state-unknown',
}

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
  missing_input: 'missing_input',
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
    const title = computed(() => {
      const custom = props.data && props.data.title
      if (custom) return custom
      return actionLabel(spec.value, locale.value) || props.data.nodeType
    })
    const noCancel = computed(() => {
      const cancel = spec.value && spec.value.action && spec.value.action.cancel
      return !!(cancel && cancel.cancellable === false)
    })
    function formatSeconds(v) {
      const n = Number(v)
      if (!Number.isFinite(n)) return v == null ? '' : String(v)
      const r = Math.round(n * 10) / 10
      return Number.isInteger(r) ? String(r) : r.toFixed(1)
    }
    const isActionMissing = computed(() => kind.value === 'missing')
    const isMissingInput = computed(() => kind.value === 'missing_input')
    const emptyPorts = computed(() => {
      const set = new Set()
      if (!isMissingInput.value) return set
      const params = (props.data && props.data.params) || {}
      for (const p of ins.value) {
        const v = params[p.name]
        if (v == null || v === '') set.add(p.name)
      }
      const listed = (spec.value && spec.value.action && spec.value.action.params) || []
      for (const p of listed) {
        if (!p || !p.name) continue
        const v = params[p.name]
        if ((p.secret || p.required) && (v == null || v === '')) set.add(p.name)
      }
      return set
    })
    const portMissing = (port) => !!(port && emptyPorts.value.has(port.name))
    const isTimeout = computed(() => {
      const s = st.value
      if (s.status === 'interrupted_unknown') return false
      return s.error_type === 'timeout' || s.error === 'timeout'
    })
    const word = computed(() => {
      void locale.value
      return statusWord(st.value)
    })
    const summary = computed(() => {
      void locale.value
      const s = st.value
      if (s.no_cancel_too_long) {
        return t('run.no_cancel_too_long', {
          node: s.node_label || title.value,
          s: formatSeconds(s.timeout_s),
        })
      }
      if (isActionMissing.value) return t('error.action_missing')
      if (kind.value === 'unknown') {
        const code = s.error_type || s.error
        return (code && mapError(code)) || t('error.may_have_written')
      }
      if (kind.value === 'error') {
        const code = s.error_type || s.error
        if (code) return mapError(code, { s: s.timeout_s || s.waited_s || s.wait_s })
        if (isTimeout.value) return t('error.timeout')
        return s.error_summary || ''
      }
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
          const item = list.find(x => x.name === name)
          return item && item.side_effect === 'write'
        })
      }
      if (!s) return false
      if (s.shows_write_mark != null) return !!s.shows_write_mark
      return s.side_effect === 'write' || s.side_effect === 'motion'
    })
    const rows = computed(() => Math.max(ins.value.length, outs.value.length))
    const bpBefore = computed(() => (props.data.breakpoints || {}).before || {})
    const bpAfter = computed(() => (props.data.breakpoints || {}).after || {})
    const srcOf = (port) => (portSources.value || portSources)[props.id + ':' + port]
    const icoUrl = (name) => 'url("' + iconBase + '/' + name + '.svg")'
    const sideTitle = computed(() => {
      void locale.value
      return (spec.value && spec.value.side_effect === 'motion') ? t('side.motion') : t('side.write')
    })
    const bpBeforeTitle = computed(() => { void locale.value; return bpBefore.value.enabled ? t('bp.before_on') : t('bp.before_off') })
    const bpAfterTitle = computed(() => { void locale.value; return bpAfter.value.enabled ? t('bp.after_on') : t('bp.after_off') })
    return {
      Position, ins, outs, st, word, write, rows, spec, bpBefore, bpAfter, srcOf, toggleBp,
      kind, icon, typeIcon, iconBase, icoUrl, summary, title, noCancel, sideTitle, bpBeforeTitle, bpAfterTitle, t, locale, portLabel,
      isActionMissing, isMissingInput, portMissing,
    }
  },
  template: `
  <div class="fn" :class="{ 'no-ports': !rows }" :data-status="kind" :data-state="st.status" :data-node-type="data.nodeType">
    <div class="bp before" :class="{ on: bpBefore.enabled, cond: bpBefore.round || (bpBefore.condition && bpBefore.condition.field) }"
         :title="bpBeforeTitle"
         @click.stop="toggleBp(id, 'before')">
      <span class="dot"></span>
    </div>
    <div class="bp after" :class="{ on: bpAfter.enabled, cond: bpAfter.round || (bpAfter.condition && bpAfter.condition.field) }"
         :title="bpAfterTitle"
         @click.stop="toggleBp(id, 'after')">
      <span class="dot"></span>
    </div>
    <div class="fn-title">
      <span v-if="typeIcon" class="fn-type-ico" :style="{ '--ico': icoUrl(typeIcon) }"></span>
      <span v-if="write" class="fn-side" :style="{ '--ico': icoUrl('mark-write') }" :title="sideTitle"></span>
      <span class="fn-name" :class="{ full: isActionMissing }">{{ title }}</span>
      <span v-if="noCancel" class="fn-no-cancel" data-testid="node-no-cancel">{{ t('node.no_cancel') }}</span>
      <span v-if="isActionMissing" class="fn-gone-x" aria-hidden="true">×</span>
      <span class="fn-st" v-else>
        <span class="ico" :data-kind="kind" :style="{ '--ico': icoUrl(icon) }"></span>
        <span class="fn-st-word">{{ word }}</span>
      </span>
    </div>
    <div class="fn-err" v-if="summary" :class="{ amber: kind === 'unknown' }" :title="summary" data-testid="node-summary">{{ summary }}</div>
    <div class="fn-ports" v-if="rows">
      <div class="fn-port" v-for="n in rows" :key="n" :class="{ 'port-missing': portMissing(ins[n-1]) }">
        <template v-if="ins[n-1]">
          <Handle type="target" :position="Position.Left" :id="ins[n-1].name"
                  :class="(srcOf(ins[n-1].name) && srcOf(ins[n-1].name).kind) || ''" />
          <span class="fn-port-name">{{ portLabel(ins[n-1]) }}</span>
          <span class="src" v-if="srcOf(ins[n-1].name)">{{ srcOf(ins[n-1].name).text }}</span>
        </template>
        <span style="flex:1"></span>
        <template v-if="outs[n-1]">
          <span class="fn-port-name">{{ portLabel(outs[n-1]) }}</span>
          <Handle type="source" :position="Position.Right" :id="outs[n-1].name" />
        </template>
      </div>
    </div>
  </div>`
}))
