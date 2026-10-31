import { defineComponent, markRaw, computed } from 'vue'
import { BaseEdge, EdgeLabelRenderer } from '@vue-flow/core'
import { nodeStatus, edgeRound } from '../status-store.js'
import { t, locale } from '../i18n.js'

export default markRaw(defineComponent({
  name: 'LoopEdge',
  props: ['id', 'sourceX', 'sourceY', 'targetX', 'targetY', 'data', 'markerEnd', 'target'],
  components: { BaseEdge, EdgeLabelRenderer },
  setup(props) {
    const geo = computed(() => {
      const dip = Math.max(props.sourceY, props.targetY) + 110
      const sx = props.sourceX, sy = props.sourceY, tx = props.targetX, ty = props.targetY
      const path = `M ${sx} ${sy} C ${sx + 80} ${sy}, ${sx + 80} ${dip}, ${sx} ${dip} L ${tx} ${dip} C ${tx - 80} ${dip}, ${tx - 80} ${ty}, ${tx} ${ty}`
      return { path, lx: (sx + tx) / 2, ly: dip }
    })
    const label = computed(() => {
      void locale.value
      const r = (nodeStatus[props.target] && nodeStatus[props.target].round) || edgeRound[props.id] || (props.data && props.data.round) || 0
      return t('loop.round', { round: r || 1 })
    })
    return { geo, label }
  },
  template: `
  <BaseEdge :id="id" :path="geo.path" :marker-end="markerEnd"
            :style="{ strokeDasharray: '6 4', stroke: 'var(--edge)', strokeWidth: 1.5 }" />
  <EdgeLabelRenderer>
    <div class="loop-label nodrag nopan"
         :style="{ transform: 'translate(-50%,-50%) translate(' + geo.lx + 'px,' + geo.ly + 'px)' }">{{ label }}</div>
  </EdgeLabelRenderer>`
}))
