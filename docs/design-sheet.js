import { createApp, defineComponent, provide, reactive, onMounted, computed } from 'vue'
import { VueFlow, MarkerType } from '@vue-flow/core'
import FlowNode from '../web/js/components/FlowNode.js'
import LoopEdge from '../web/js/components/LoopEdge.js'
import { nodeStatus, applyNodeStates } from '../web/js/status-store.js'

const ICON = '../web/vendor/icons'

const TYPES = [
  { type: 'start', label: '开始', icon: 'node-start', inputs: [], outputs: [{ name: 'data', label: '用户消息' }] },
  { type: 'output', label: '输出', icon: 'node-output', inputs: [{ name: 'value', label: '回复' }], outputs: [] },
  { type: 'constant', label: '常量', icon: 'node-constant', inputs: [], outputs: [{ name: 'value', label: '值' }] },
  { type: 'text_template', label: '文本模板', icon: 'node-template', dynamic_inputs: 'slots', default_params: { slots: ['用户消息', '背景'] }, inputs: [{ name: '用户消息', label: '用户消息' }, { name: '背景', label: '背景' }], outputs: [{ name: 'text', label: '提示词' }] },
  { type: 'model_call', label: '模型调用', icon: 'node-model', side_effect: 'read', inputs: [{ name: 'prompt', label: '提示词' }, { name: 'history', label: '历史' }], outputs: [{ name: 'reply', label: '回复' }, { name: 'tool_calls', label: '工具请求' }] },
  { type: 'tool_call', label: '工具调用', icon: 'node-tool', side_effect: 'write', shows_write_mark: true, side_effect_note: '会写外部：往演示笔记追加一行', inputs: [{ name: 'args', label: '请求' }], outputs: [{ name: 'result', label: '结果' }], default_params: { tool: 'append_note', allowed_tools: ['append_note'] } },
  { type: 'condition', label: '条件判断', icon: 'node-condition', inputs: [{ name: 'value', label: '回复' }], outputs: [{ name: 'yes', label: '是' }, { name: 'no', label: '否' }] },
  { type: 'merge', label: '合并', icon: 'node-merge', dynamic_inputs: 'ports', default_params: { ports: ['a', 'b'] }, inputs: [{ name: 'a', label: 'a' }, { name: 'b', label: 'b' }], outputs: [{ name: 'merged', label: '合并结果' }] },
  { type: 'human_confirm', label: '人工确认', icon: 'node-approval', inputs: [{ name: 'value', label: '请求' }], outputs: [{ name: 'pass', label: '通过' }, { name: 'reject', label: '驳回' }] },
]

const TOOLS = [
  { name: 'append_note', side_effect: 'write' },
  { name: 'read_note', side_effect: 'read' },
  { name: 'clock', side_effect: 'none' },
]

function nd(id, type, x, y, title, extra) {
  extra = extra || {}
  const spec = TYPES.find(t => t.type === type)
  return {
    id, type: 'agent', position: { x, y }, draggable: false, selectable: false,
    data: {
      nodeType: type,
      title: title || (spec && spec.label) || type,
      params: extra.params || (spec && spec.default_params) || {},
      breakpoints: extra.breakpoints || {},
    },
  }
}

function edge(id, src, sh, tgt, th, kind) {
  return {
    id, source: src, target: tgt, sourceHandle: sh, targetHandle: th,
    type: kind === 'loop' ? 'loop' : 'default',
    animated: kind === 'active',
    markerEnd: MarkerType.ArrowClosed,
  }
}

const LOOP_NODES = [
  nd('start', 'start', 20, 150, '开始'),
  nd('const', 'constant', 20, 340, '常量'),
  nd('tpl', 'text_template', 290, 150, '文本模板', { params: { slots: ['用户消息', '背景'] } }),
  nd('model', 'model_call', 560, 150, '模型调用', { breakpoints: { before: { enabled: true, round: 2 } } }),
  nd('cond', 'condition', 830, 150, '条件判断'),
  nd('tool', 'tool_call', 1100, 150, '工具调用', { params: { tool: 'append_note', allowed_tools: ['append_note'] }, breakpoints: { before: { enabled: true } } }),
  nd('out', 'output', 1100, 340, '输出'),
]
const LOOP_EDGES = [
  edge('e0', 'start', 'data', 'tpl', '用户消息'),
  edge('e1', 'const', 'value', 'tpl', '背景'),
  edge('e2', 'tpl', 'text', 'model', 'prompt'),
  edge('e3', 'model', 'reply', 'cond', 'value'),
  edge('e4', 'cond', 'yes', 'tool', 'args', 'active'),
  edge('e5', 'cond', 'no', 'out', 'value'),
  edge('e6', 'tool', 'result', 'model', 'history', 'loop'),
]

const LOOP_DONE = {
  start: { status: 'done' },
  const: { status: 'done' },
  tpl: { status: 'done', duration_ms: 1 },
  model: { status: 'done', duration_ms: 2400, round: 3 },
  cond: { status: 'done', duration_ms: 1 },
  out: { status: 'idle' },
}

function prefixGraph(prefix, nodes, edges) {
  const n = nodes.map(x => ({ ...x, id: prefix + x.id, data: { ...x.data } }))
  const e = edges.map(x => ({ ...x, id: prefix + x.id, source: prefix + x.source, target: prefix + x.target }))
  return { nodes: n, edges: e }
}

function prefixStatus(prefix, map) {
  const out = {}
  for (const [k, v] of Object.entries(map)) out[prefix + k] = v
  return out
}

const TopBar = {
  props: ['mode', 'word', 'wordKind', 'steps', 'conn', 'theme'],
  computed: {
    s1() {
      return { idle: '运行', pausing: '暂停中…', paused: '继续', await: '继续', offline: '继续', error: '运行' }[this.mode] || '运行'
    },
    s1off() { return this.mode === 'pausing' || this.mode === 'await' || this.mode === 'offline' },
    s2off() { return this.mode !== 'paused' },
    s3off() { return this.mode === 'idle' || this.mode === 'error' || this.mode === 'offline' },
  },
  template: `
  <div class="top">
    <span class="name">客服回复 agent</span>
    <span class="ver">v12</span>
    <div class="ctrls">
      <div class="slots">
        <button class="primary slot-1" :disabled="s1off">{{ s1 }} <span class="kbd" v-if="s1==='运行' || s1==='继续'">F5</span></button>
        <button class="slot-2" :disabled="s2off">单步 <span class="kbd" v-if="!s2off">F10</span></button>
        <button class="slot-3" :disabled="s3off">停止</button>
        <span class="run-word" :class="wordKind">{{ word }}</span>
      </div>
    </div>
    <div class="right">
      <span class="steps">{{ steps }}</span>
      <span><i class="dot-conn" :class="{off: conn!=='已连接'}"></i> {{ conn }}</span>
    </div>
  </div>`
}

const SceneFlow = {
  components: { VueFlow },
  props: ['fid', 'nodes', 'edges', 'zoom'],
  setup() {
    return { nodeTypes: { agent: FlowNode }, edgeTypes: { loop: LoopEdge } }
  },
  template: `
  <VueFlow :id="fid" :nodes="nodes" :edges="edges" :node-types="nodeTypes" :edge-types="edgeTypes"
           :min-zoom="0.2" :max-zoom="2" :nodes-draggable="false" :nodes-connectable="false"
           :pan-on-drag="false" :zoom-on-scroll="false" :default-viewport="{ x: 16, y: 70, zoom: zoom || 0.72 }" />`
}

const App = defineComponent({
  components: { TopBar, SceneFlow },
  setup() {
    const types = TYPES
    const portSources = reactive({})
    provide('nodeTypes', types)
    provide('portSources', portSources)
    provide('toggleBp', () => {})
    provide('tools', TOOLS)
    provide('iconBase', ICON)

    const al = prefixGraph('al-', LOOP_NODES, LOOP_EDGES)
    const ad = prefixGraph('ad-', LOOP_NODES, LOOP_EDGES)
    const rp = prefixGraph('rp-', LOOP_NODES, LOOP_EDGES)
    const raNodes = LOOP_NODES.map(n => n.id === 'tool'
      ? nd('tool', 'human_confirm', 1100, 150, '人工确认')
      : n)
    const ra = prefixGraph('ra-', raNodes, LOOP_EDGES)
    const ro = prefixGraph('ro-', LOOP_NODES, LOOP_EDGES)

    const bNodes = [
      nd('tpl', 'text_template', 20, 150, '文本模板', { params: { slots: ['用户消息'] } }),
      nd('model', 'model_call', 316, 190, '模型调用'),
      nd('tool', 'tool_call', 584, 190, '工具调用', { params: { tool: 'append_note', allowed_tools: ['append_note'] } }),
    ]
    const bEdges = [
      edge('e0', 'tpl', 'text', 'model', 'prompt'),
      edge('e1', 'model', 'tool_calls', 'tool', 'args'),
    ]
    const bl = prefixGraph('bl-', bNodes, bEdges)

    const STATES = [
      ['idle', 'idle', '模型调用', 'model_call', {}],
      ['queued', 'queued', '模型调用', 'model_call', {}],
      ['run', 'running', '模型调用', 'model_call', {}],
      ['pause', 'paused_before', '模型调用', 'model_call', {}],
      ['restart', 'paused_before', '模型调用', 'model_call', { pause_reason: 'restart' }],
      ['done', 'done', '模型调用', 'model_call', { duration_ms: 1200 }],
      ['err', 'error', '模型调用', 'model_call', { error_summary: '超时', error_type: 'timeout' }],
      ['skip', 'skipped', '模型调用', 'model_call', {}],
      ['stopped', 'stopped', '模型调用', 'model_call', {}],
      ['interrupted', 'interrupted', '模型调用', 'model_call', {}],
      ['unknown', 'interrupted_unknown', '工具调用', 'tool_call', { error_summary: '外部可能已写入' }],
      ['await', 'awaiting_approval', '人工确认', 'human_confirm', {}],
    ]
    function stateNodes(prefix) {
      return STATES.map((row, i) => {
        const [key, , title, type] = row
        const params = type === 'tool_call' ? { tool: 'append_note', allowed_tools: ['append_note'] } : {}
        return nd(prefix + key, type, 40 + (i % 6) * 270, 70 + Math.floor(i / 6) * 130, title, { params })
      })
    }
    const PORTS = [
      ['up', '', '真实上游'],
      ['cache', 'cache', '缓存 · 15:42 · 第 2 轮'],
      ['stale', 'stale', '缓存 · 上游已改'],
      ['pin', 'pinned', '已固定'],
      ['manual', 'manual', '手填'],
      ['unused', 'pinned-unused', '本次未用固定值'],
    ]
    function portNodes(prefix) {
      return PORTS.map((row, i) => nd(prefix + row[0], 'model_call', 40 + (i % 3) * 400, 380 + Math.floor(i / 3) * 80, '输入端口'))
    }
    const clNodes = stateNodes('cl-').concat(portNodes('cl-')).concat([
      nd('cl-sel', 'tool_call', 60, 730, '工具调用', { params: { tool: 'append_note', allowed_tools: ['append_note'] } }),
      nd('cl-lod', 'model_call', 460, 730, '模型调用'),
    ])
    const cdNodes = stateNodes('cd-').concat(portNodes('cd-')).concat([
      nd('cd-sel', 'tool_call', 60, 730, '工具调用', { params: { tool: 'append_note', allowed_tools: ['append_note'] } }),
      nd('cd-lod', 'model_call', 460, 730, '模型调用'),
    ])
    const cgNodes = stateNodes('cg-').concat(portNodes('cg-')).concat([
      nd('cg-sel', 'tool_call', 60, 730, '工具调用', { params: { tool: 'append_note', allowed_tools: ['append_note'] } }),
      nd('cg-lod', 'model_call', 460, 730, '模型调用'),
    ])

    onMounted(() => {
      applyNodeStates(prefixStatus('al-', { ...LOOP_DONE, tool: { status: 'paused_before' } }))
      applyNodeStates(prefixStatus('ad-', { ...LOOP_DONE, tool: { status: 'paused_before' } }))
      applyNodeStates(prefixStatus('rp-', { ...LOOP_DONE, tool: { status: 'running' } }))
      applyNodeStates(prefixStatus('ra-', { ...LOOP_DONE, tool: { status: 'awaiting_approval' } }))
      applyNodeStates(prefixStatus('ro-', { ...LOOP_DONE, tool: { status: 'paused_before' } }))
      applyNodeStates(prefixStatus('bl-', {
        tpl: { status: 'done', duration_ms: 1 },
        model: { status: 'done', duration_ms: 2100 },
        tool: { status: 'interrupted_unknown', error_summary: '外部可能已写入' },
      }))
      function applyStates(prefix) {
        const map = {}
        for (const [key, status, , , extra] of STATES) map[prefix + key] = { status, ...extra }
        map[prefix.replace(/-$/, '') ? prefix + 'sel' : ''] = {}
        applyNodeStates(map)
        applyNodeStates({ [prefix + 'sel']: { status: 'error', error_summary: '超时', error_type: 'timeout' } })
        applyNodeStates({ [prefix + 'lod']: { status: 'running' } })
      }
      applyStates('cl-')
      applyStates('cd-')
      applyStates('cg-')

      portSources['cl-cache:prompt'] = { kind: 'cache', text: '缓存 · 15:42 · 第 2 轮' }
      portSources['cl-stale:prompt'] = { kind: 'stale', text: '缓存 · 上游已改' }
      portSources['cl-pin:prompt'] = { kind: 'pinned', text: '已固定' }
      portSources['cl-manual:prompt'] = { kind: 'manual', text: '手填' }
      portSources['cl-unused:prompt'] = { kind: 'pinned-unused', text: '本次未用固定值' }
      for (const p of ['cd-', 'cg-']) {
        portSources[p + 'cache:prompt'] = portSources['cl-cache:prompt']
        portSources[p + 'stale:prompt'] = portSources['cl-stale:prompt']
        portSources[p + 'pin:prompt'] = portSources['cl-pin:prompt']
        portSources[p + 'manual:prompt'] = portSources['cl-manual:prompt']
        portSources[p + 'unused:prompt'] = portSources['cl-unused:prompt']
      }
      portSources['bl-model:prompt'] = { kind: 'stale', text: '缓存 · 上游已改' }
      portSources['bl-model:history'] = { kind: 'pinned', text: '已固定' }

      requestAnimationFrame(() => { window.__sheetReady = true })
    })

    return {
      al, ad, rp, ra, ro, bl,
      clNodes, cdNodes, cgNodes,
      MarkerType,
    }
  },
  template: `
  <div>
    <div class="sheet-intro">
      <h1>设计对照页</h1>
      <p>用真实节点组件按 Rams 的 mockup 场景排版：全布局（浅/深）、暂停中、等待确认、离线、分组+出错、全部状态（彩色/深色/灰度）。标签按 Thompson：「中断」「中断 · 结果未知」。write 超时画成结果未知，不画成出错。</p>
    </div>

    <div class="shot w1440" data-shot="a-light" data-theme="light">
      <TopBar mode="paused" word="已暂停 · 断点" word-kind="amber" steps="步数 11 / 上限 1000" conn="已连接" />
      <div class="canvas-slot"><SceneFlow fid="al" :nodes="al.nodes" :edges="al.edges" :zoom="0.72" /></div>
      <aside class="right">
        <div class="panel-head">
          <div class="t">工具调用</div>
          <div class="meta amber">已暂停 · 执行前 · 断点 · 步 11</div>
          <div class="rounds"><span>‹</span><span><b>第 3 轮</b> / 共 3 轮</span><span>›</span></div>
        </div>
        <div class="tabs"><button class="on">输入</button><button>输出</button><button>参数</button><button>记录</button></div>
        <div class="pb">
          <div class="lbl">输入来源</div>
          <div class="sel-in">真实上游 · 条件判断「是」· 第 3 轮</div>
          <div class="code">{ "工具": "append_note", "参数": { "文件": "notes.txt", "内容": "A-20417 已查询" } }</div>
        </div>
        <div class="pf"><button class="primary">继续</button><button>单步</button><span class="hint">改动只作用于这一步</span></div>
      </aside>
      <div class="zoom-chip"><span>−</span><span>72%</span><span>+</span><span>适合</span></div>
    </div>

    <div class="shot w1440" data-shot="a-dark" data-theme="dark">
      <TopBar mode="paused" word="已暂停 · 断点" word-kind="amber" steps="步数 11 / 上限 1000" conn="已连接" />
      <div class="canvas-slot"><SceneFlow fid="ad" :nodes="ad.nodes" :edges="ad.edges" :zoom="0.72" /></div>
      <aside class="right">
        <div class="panel-head">
          <div class="t">工具调用</div>
          <div class="meta amber">已暂停 · 执行前 · 断点 · 步 11</div>
        </div>
        <div class="tabs"><button class="on">输入</button><button>输出</button></div>
        <div class="pb"><div class="code">{ "工具": "append_note" }</div></div>
        <div class="pf"><button class="primary">继续</button><button>单步</button></div>
      </aside>
    </div>

    <div class="shot w1440" data-shot="run-pausing" data-theme="light">
      <TopBar mode="pausing" word="运行中" word-kind="muted" steps="步数 11 / 上限 1000" conn="已连接" />
      <div class="canvas-slot"><SceneFlow fid="rp" :nodes="rp.nodes" :edges="rp.edges" :zoom="0.72" /></div>
      <aside class="right">
        <div class="panel-head">
          <div class="t">工具调用</div>
          <div class="meta blue">运行中 · 这一步跑完就停</div>
        </div>
        <div class="tabs"><button class="on">输入</button></div>
        <div class="pb"><div class="lbl">运行中，只读</div><div class="code">{ "工具": "append_note" }</div></div>
        <div class="pf"><span class="hint">已请求暂停，等这一步跑完。</span></div>
      </aside>
    </div>

    <div class="shot w1440 has-banner" data-shot="run-await" data-theme="light">
      <TopBar mode="await" word="等待确认" word-kind="amber" steps="步数 11 / 上限 1000" conn="已连接" />
      <div class="banners">
        <div class="banner amber">
          <span>「人工确认」在等你决定：要不要把这一行写进 notes.txt。</span>
          <span class="spacer"></span>
          <button>驳回</button>
          <button class="primary">通过</button>
        </div>
      </div>
      <div class="canvas-slot"><SceneFlow fid="ra" :nodes="ra.nodes" :edges="ra.edges" :zoom="0.72" /></div>
      <aside class="right">
        <div class="panel-head">
          <div class="t">人工确认</div>
          <div class="meta amber">等待确认 · 步 11 · 第 3 轮</div>
        </div>
        <div class="tabs"><button class="on">输入</button></div>
        <div class="pb">
          <div class="lbl">要确认的内容</div>
          <div class="code">{ "工具": "append_note", "参数": { "文件": "notes.txt", "内容": "A-20417 已查询" } }</div>
          <div class="lbl">说明（可不填）</div>
          <input placeholder="写给之后看记录的人">
        </div>
        <div class="pf"><button class="primary">通过</button><button>驳回</button><span class="hint">结果会写进运行记录</span></div>
      </aside>
    </div>

    <div class="shot w1440" data-shot="run-offline" data-theme="light">
      <TopBar mode="offline" word="已暂停 · 断点" word-kind="muted" steps="步数 11 / 上限 1000" conn="重连中" />
      <div class="canvas-slot"><SceneFlow fid="ro" :nodes="ro.nodes" :edges="ro.edges" :zoom="0.72" /></div>
      <aside class="right">
        <div class="panel-head">
          <div class="t">工具调用</div>
          <div class="meta">重连中，暂不能改</div>
        </div>
        <div class="pf"><button disabled>继续</button><button disabled>单步</button><span class="hint">重连后恢复</span></div>
      </aside>
    </div>

    <div class="shot w1440 has-left has-banner" data-shot="b-light" data-theme="light">
      <TopBar mode="error" word="出错" word-kind="red" steps="步数 7 / 上限 1000" conn="已连接" />
      <aside class="left">
        <h2>节点</h2>
        <div class="nt" v-for="t in ['开始','输出','常量','文本模板','模型调用','工具调用','条件判断','合并','人工确认']" :key="t">{{ t }}</div>
      </aside>
      <div class="banners">
        <div class="banner"><span>画布已改，这次运行仍按 v12。</span><span class="spacer"></span><button>从新版本重跑</button></div>
      </div>
      <div class="canvas-slot"><SceneFlow fid="bl" :nodes="bl.nodes" :edges="bl.edges" :zoom="1" /></div>
      <aside class="right">
        <div class="panel-head">
          <div class="t">工具调用</div>
          <div class="meta amber">中断 · 结果未知 · 步 7 · 第 2 轮</div>
        </div>
        <div class="tabs"><button class="on">输入</button></div>
        <div class="pb">
          <p class="idem-note">继续时会带同一个幂等键重试，外部服务按这个键去重。</p>
          <div class="lbl">单跑用哪份输入</div>
          <div class="sel-in">10-31 15:42 · 第 2 轮</div>
          <button>真执行…</button>
        </div>
        <div class="pf"><button class="primary">只跑这个</button><button>从这里重跑</button><button>固定这份输入</button></div>
      </aside>
    </div>

    <div class="shot w1680" data-shot="c-states" data-theme="light">
      <div class="shot-cap">节点状态（彩色）</div>
      <div class="shot-sub" style="left:40px;top:350px;font-weight:600;color:var(--text)">入口端口</div>
      <div class="canvas-slot" style="left:0;right:0;top:0;bottom:0">
        <SceneFlow fid="cl" :nodes="clNodes" :edges="[]" :zoom="1" />
      </div>
    </div>

    <div class="shot w1680" data-shot="c-states-dark" data-theme="dark">
      <div class="shot-cap">节点状态（深色）</div>
      <div class="canvas-slot" style="left:0;right:0;top:0;bottom:0">
        <SceneFlow fid="cd" :nodes="cdNodes" :edges="[]" :zoom="1" />
      </div>
    </div>

    <div class="shot w1680 gray" data-shot="c-states-gray" data-theme="light">
      <div class="shot-cap">节点状态（灰度）</div>
      <div class="canvas-slot" style="left:0;right:0;top:0;bottom:0">
        <SceneFlow fid="cg" :nodes="cgNodes" :edges="[]" :zoom="1" />
      </div>
    </div>
  </div>`
})

createApp(App).mount('#sheet')
