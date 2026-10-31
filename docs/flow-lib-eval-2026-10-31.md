# 流程画布库评估（第一部分：流程编辑器）

日期：2026-10-31。下文的 npm 版本、发布日期、star 数和 issue 数都在当天读取。实测数字来自本机 spike（`/workspace/agent-ui/spike/`），跑在 headless Chrome + 软件 GL 上，不是 Alex 的电脑。

**结论（建议）：用 Vue Flow 1.48.2。** 在「零构建 + 离线 vendored + 必须支持环 + 节点用 DOM 渲染」这几条硬约束下，它是唯一一个同时满足下面几点的候选：直接用 npm 包里的原文件（sha256 与 tarball 一致）、用 import map 就能跑、实测 500 节点不卡。代价有三个：要加一行 `process` shim；维护节奏比 React Flow 慢；没有内置撤销和复制粘贴。React Flow 作为备选，实测性能略好，但 React 只能先经 esm.sh 转码再 vendor，这一步不是官方产物。LiteGraph 性能最好，但节点是 canvas 画的，中文换行、tooltip 和点击检测都要自己写，排除。

---

## 1. 对比表

| | Vue Flow 1.48.2 | React Flow (@xyflow/react 12.12.0) | Rete v2 | LiteGraph（jagenjo 0.7.18 / @comfyorg 0.17.2） | Drawflow 0.0.60 |
|---|---|---|---|---|---|
| **零构建 + 离线（置顶）** | ✅ 实测通过。6 个 npm 原文件，627,597 B；唯一外部 import 是 `vue`（d3 和 @vueuse 已打进 dist）。需要 `process` shim，见 [log.ts L9-11](https://github.com/bcakmakoglu/vue-flow/blob/master/packages/core/src/utils/log.ts#L9-L11)。依赖：[package.json](https://registry.npmjs.org/@vue-flow/core/1.48.2) | ⚠️ 实测通过，但 React 19 [已不提供 UMD](https://react.dev/blog/2024/04/25/react-19-upgrade-guide#umd-builds-removed)，npm 包是 CJS，只能用 [esm.sh](https://esm.sh) 转成 ESM 后 vendor：13 个文件，411,384 B。没有 JSX，所以要另外 vendor [htm](https://github.com/developit/htm) | ✅ smoke 测试离线通过（UMD + Vue 全局，`smoke/rete-offline.html`），要 6 个插件文件。依赖：[rete package.json](https://registry.npmjs.org/rete/2.0.6) | ✅ 实测通过。@comfyorg 版是 1 个 es.js 文件、零 import；jagenjo 版是 1 个全局脚本。依赖为 0（[npm](https://registry.npmjs.org/@comfyorg/litegraph/0.17.2)） | ✅ 一个 UMD 文件，零依赖（[README](https://github.com/jerosoler/Drawflow#readme)）。spike 没测 |
| 无限画布 / 性能 | 500 节点实测 p95 16.7 ms；LOD 改动节点尺寸时有长任务（见 §6）。有 [onlyRenderVisibleElements](https://vueflow.dev/guide/vue-flow/config.html#only-render-visible-elements-optional) | 500 节点实测 p95 16.8 ms；长任务 52–98 ms。见[性能指南](https://reactflow.dev/learn/advanced-use/performance) | 没实测，也没找到官方性能文档（推断：取决于选的渲染插件） | 500 节点 p95 16.7 ms，0 个长任务（canvas）。缩放低于 0.6 自动降级，见 [L8551](https://github.com/jagenjo/litegraph.js/blob/master/src/litegraph.js#L8551) | 没有资料。推断：DOM 加 SVG，规模大了会慢 |
| 分组 | 父子节点，见 [nesting 示例](https://vueflow.dev/examples/nodes/nesting.html) | [Sub flows](https://reactflow.dev/learn/layouting/sub-flows) | [scopes 插件](https://retejs.org/docs/guides/scopes) | [LGraphGroup](https://github.com/jagenjo/litegraph.js/blob/master/src/litegraph.js#L4990) 只是一个框，没有端口 | README 没写分组（推断：不支持） |
| 自定义节点 / 端口 / 连线校验 | Vue 模板 + [Handle 和 isValidConnection](https://vueflow.dev/guide/handle.html) | htm 组件 + [validation](https://reactflow.dev/examples/interaction/validation) | 有，需要 render 插件 | 继承 LGraphNode，在 canvas 上画 | HTML 字符串 |
| 节点状态叠加 / 边动画 | 实测：状态点和中文标签都正常；`animated` 边可用 | 实测同样可以；[边动画](https://reactflow.dev/examples/edges/animating-edges) | 推断：自己写 | 要在 onDrawForeground 里画；虚线和标签要重写 renderLink（[L9494](https://github.com/jagenjo/litegraph.js/blob/master/src/litegraph.js#L9494)，spike 里写了约 20 行） | 推断：用 CSS |
| 执行引擎 | 无（执行在 Python 端，不需要） | 无 | [DataflowEngine 和 ControlFlowEngine](https://retejs.org/docs/concepts/engine) | 自带 [computeExecutionOrder](https://github.com/jagenjo/litegraph.js/blob/master/src/litegraph.js#L1161) | 无 |
| 序列化 / 撤销 / 复制 / 快捷键 / 多选 / 自动布局 | 有 [toObject 存取](https://vueflow.dev/examples/save.html) 和多选；没有撤销和复制（推断：要自己写）；布局用 [dagre 示例](https://vueflow.dev/examples/layout/simple.html)，dagre 也要 vendor | 有序列化和多选；[撤销/重做](https://reactflow.dev/examples/interaction/undo-redo)是 **Pro 示例**；[自动布局示例](https://reactflow.dev/examples/layout/auto-layout) | 有序列化。推断：撤销要自己写 | [serialize](https://github.com/jagenjo/litegraph.js/blob/master/src/litegraph.js#L2185)、[copyToClipboard](https://github.com/jagenjo/litegraph.js/blob/master/src/litegraph.js#L7232)、[beforeChange（撤销钩子）](https://github.com/jagenjo/litegraph.js/blob/master/src/litegraph.js#L2089) | export/import JSON |
| 维护 / 许可 / Pro | MIT；6,926★，19 个 open issue；最后发版 1.48.2，2026-01-28（[npm](https://registry.npmjs.org/@vue-flow/core)）；仓库最近 push 2026-07-14（[GitHub](https://github.com/bcakmakoglu/vue-flow)） | MIT；38,847★，151 个 open issue；12.12.0 发布于 2026-09-24；仓库最近 push 2026-10-29（[GitHub](https://github.com/xyflow/xyflow)）。有付费 [Pro](https://reactflow.dev/pro) | MIT；12,299★；rete 2.0.6 发布于 2025-06-30（[GitHub](https://github.com/retejs/rete)） | jagenjo 版最后 push 2024-08-01，146 个 open issue（[GitHub](https://github.com/jagenjo/litegraph.js)）。@comfyorg 独立仓库**已归档**（[GitHub](https://github.com/Comfy-Org/litegraph.js)），代码已并入 [ComfyUI_frontend/src/lib/litegraph](https://github.com/Comfy-Org/ComfyUI_frontend/tree/main/src/lib/litegraph)，那边要 [vite build](https://github.com/Comfy-Org/ComfyUI_frontend/blob/main/package.json#L26)；npm 最后一版 0.17.2 发布于 2025-08-06 | MIT；6,169★，274 个 open issue；最后 push 2024-10-19（[GitHub](https://github.com/jerosoler/Drawflow)） |
| 与 vue-vet / pixi-glyphflow 的配合 | 推断：同是 Vue，最顺 | 推断：要再引入一套 React | 推断：可以选 Vue 渲染 | 推断：canvas 那一套和 pixi 思路接近，但和 DOM 组件不通 | — |

---

## 2. 各库卡在哪

**Vue Flow**
- 用 dist 原文件时会报 `ReferenceError: process is not defined`（vue-flow-core.mjs:4663 的 `isDev()`）。原因是源码里的 `__ENV__` 在构建时被替换成了 `process.env.NODE_ENV`（[log.ts L9-11](https://github.com/bcakmakoglu/vue-flow/blob/master/packages/core/src/utils/log.ts#L9-L11)）。修法是在 import map 前加一行 `globalThis.process ??= { env: { NODE_ENV: "production" } }`，加上之后 console 零错误。
- 不能用 SFC，只能写模板字符串。为此必须 vendor 带编译器的 `vue.esm-browser.prod.js`（[Vue 文档](https://vuejs.org/guide/quick-start.html#enabling-import-maps)）。
- LOD 切换时如果改变了节点尺寸（比如隐藏标签），500 个节点会一起重新测量，产生 250–297 ms 的长任务。改成只隐藏、不改尺寸后，长任务为 0（§6）。
- 开 onlyRenderVisibleElements 后，平移时 p95 反而升到 33.3 ms（挂载和卸载太频繁）。不建议开。
- 没有撤销和复制粘贴，要自己写（推断）。发版节奏比 React Flow 慢。
- minimap 自带一份 d3（99 KB）。

**React Flow**
- 运行时依赖的文件不是 npm 原文件，而是 esm.sh 转码后的产物。升级时要重跑 `reactflow/vendor_fetch.py`，并重新核对 sha256。第一次跑时还漏了 `./react-dom.mjs` 这种相对路径的 import，已修复（当时报 404）。
- 没有 JSX，所以要用 htm，写起来和官方示例都不一样。
- 撤销/重做、分组选择等示例是 Pro 内容（[undo-redo](https://reactflow.dev/examples/interaction/undo-redo) 页面写着 "About this Pro Example"）。

**Rete v2**
- 要拼 6 个插件，渲染层还要再选 Vue 或 React。DataflowEngine 递归求值，推断它遇到环会出问题；控制流引擎可以用（[文档](https://retejs.org/docs/guides/processing/control-flow)）。执行放在 Python 端的话，这部分用不上。

**LiteGraph**
- 节点是 canvas 画的。`fillText` 不会自动换行（[MDN](https://developer.mozilla.org/en-US/docs/Web/API/CanvasRenderingContext2D/fillText)），spike 里中文换行是手写的。tooltip、可选中的文字和 icon+文字的错误态都要自己画、自己做点击检测（推断：每种交互都要写一套 hit-test）。
- 每条连线单独设样式和标签没有 API，要包一层 renderLink 来画（spike 写了约 20 行）。自画的路径点不中（推断）。
- 版本问题：jagenjo 版基本停更；Comfy 的 fork 独立仓库已归档，新代码在 ComfyUI_frontend 里，要构建才能用。vendor 0.17.2 等于冻结在一个不再更新的版本上。

**Drawflow**
- 没有分组（推断），一个人维护，274 个 open issue。只适合小 demo。

**Svelte Flow**：组件要 Svelte 编译器编译（推断），违反零构建，不考虑。

---

## 3. 两个额外问题

**几百个节点同时改状态，React Flow 和 Vue Flow 扛得住吗？** 扛得住。实测 500 个节点每 100 ms 全部切换一次状态、持续 2 s（只改状态，不平移），两个库的 p95 都是 16.7–16.8 ms，**一个长任务都没有**（`results.json → phases`）。Vue Flow 用 `updateNodeData` 和用独立的 reactive 状态表结果几乎一样。React Flow 的 `updateNodeData`、独立 store（useSyncExternalStore）和整体 `setNodes` 三种写法也差不多。

**500 节点现实吗？** 现实。下面是建议的测量方法（阈值是推断）：

- 图：500 个节点，549 条边（一条链，每 10 个节点一条回边）。
- 步骤：挂载后等 2 s，然后依次平移/缩放 2 s、只切状态 2 s、两者同时 3 s。
- 指标：用 rAF 记帧间隔，用 PerformanceObserver 记长任务（50 ms 以上算长任务，[MDN](https://developer.mozilla.org/en-US/docs/Web/API/PerformanceLongTaskTiming)），逐个记下时间点。
- 门槛（推断）：每个阶段 p95 ≤ 33 ms，挂载后没有超过 100 ms 的长任务。必须在 Alex 的机器上用真 GPU 复测一次。

---

## 4. 设计需求逐条对照

| 需求 | Vue Flow | React Flow | LiteGraph | Rete |
|---|---|---|---|---|
| 数据固定（pinning） | 推断：库里没有，靠节点 data 加 Python 执行器实现 | 同左 | 同左 | 同左 |
| 分组框 + 入口端口 | 父节点本身可以放 Handle（推断，可行）；[nesting](https://vueflow.dev/examples/nodes/nesting.html) | 同理，见 [sub flows](https://reactflow.dev/learn/layouting/sub-flows) | Group 没有端口（[L4990](https://github.com/jagenjo/litegraph.js/blob/master/src/litegraph.js#L4990)） | scopes（推断：要改插件） |
| 前后断点 | 推断：靠节点装饰加 Python 端的 asyncio.Event，库不关心 | 同左 | 同左 | 同左 |
| 缩放 LOD | 自己监听 zoom（spike 已经做了，阈值 0.6） | `useStore(s => s.transform[2] < 0.6)`（spike 已做） | 内置（[L8551](https://github.com/jagenjo/litegraph.js/blob/master/src/litegraph.js#L8551)） | 推断：自己写 |
| **环** | 实测：A→B→C→A 能渲染，鼠标也能连出 F→D 形成第二个环 | 实测同上；防环是[可选示例](https://reactflow.dev/examples/interaction/prevent-cycles)，默认允许 | 实测同上；执行顺序会把环里的节点追加在最后（[L1266](https://github.com/jagenjo/litegraph.js/blob/master/src/litegraph.js#L1266)） | 控制流可以；数据流推断会出问题 |

---

## 5. PRD 第 9 节：回答 Torvalds 的问题

**1. 哪些库不用构建就能通过 ES module 或 import map 加载？各有几个依赖？**
- Vue Flow：可以，ESM 加 import map。npm 声明了 5 个依赖（@vueuse/core 和 4 个 d3 包）加 peer `vue`，但 dist 已经把它们打进去了，运行时只 import `vue`（[package.json](https://registry.npmjs.org/@vue-flow/core/1.48.2)；spike 实测）。
- React Flow：不能直接加载。npm 包依赖 classcat、zustand、@xyflow/system（后者又依赖 4 个 d3 包），peer 是 react 和 react-dom（[package.json](https://registry.npmjs.org/@xyflow/react/12.12.0)）。React 19 没有 UMD，只能用 esm.sh 转码后 vendor。
- LiteGraph：可以。0 个依赖（[npm](https://registry.npmjs.org/litegraph.js/0.7.18)，[@comfyorg](https://registry.npmjs.org/@comfyorg/litegraph/0.17.2)）。
- Drawflow：可以，UMD，0 个依赖（[npm](https://registry.npmjs.org/drawflow/0.0.60)）。
- Rete.js：可以，用 UMD。每个包依赖 @babel/runtime，插件 peer 是 rete 和 area 等，Vue 渲染还要 `vue`（[rete](https://registry.npmjs.org/rete/2.0.6)，[rete-vue-plugin](https://registry.npmjs.org/rete-vue-plugin/2.1.3)）。

**2. 验收项 19（旧稿误写成 8.13）填什么数字（推断，等 Alex 机器复测确认）：** 500 个节点、约 550 条边，所有节点每秒改 10 次状态，同时平移和缩放。帧时间 p95 ≤ 33 ms；挂载后没有超过 100 ms 的长任务。测法按 §3，复现脚本是 `spike/run_phases.py`。

**补充：ComfyUI 当年就是这么做的。** 早期 ComfyUI 用 Python aiohttp 直接托管 `web/` 目录，页面里用普通 `<script>` 引 `lib/litegraph.core.js`，再用 `type="module"` 引 app.js，完全没有构建（[web/index.html L7-14 @v0.0.1](https://github.com/comfyanonymous/ComfyUI/blob/v0.0.1/web/index.html#L7-L14)，[server.py L86/L124 @v0.0.1](https://github.com/comfyanonymous/ComfyUI/blob/v0.0.1/server.py#L86)）。后来前端拆成了 ComfyUI_frontend，改用 Vue 加 Vite 构建（[package.json L26](https://github.com/Comfy-Org/ComfyUI_frontend/blob/main/package.json#L26)）。为什么迁移没找到出处。

---

## 6. 验证（spike）

**复现**
```
cd /workspace/agent-ui/spike
python3 -m http.server 8800 --bind 127.0.0.1 &
.venv/bin/python run_demos.py                       # Vue/React demo：截图、鼠标连边、离线检查
.venv/bin/python run_spike.py                       # 首轮：LiteGraph + Vue Flow 压测
.venv/bin/python run_phases.py vueflow  "n=500&mode=data" "n=500&mode=data&lod=keep"
.venv/bin/python run_phases.py reactflow "n=500&mode=data" "n=500&mode=map" "n=500&mode=array"
python3 make_manifest.py                            # 生成 vendor-manifest.json
```
Playwright 把所有非 localhost 的请求 abort 掉。所有 run 里 `blocked = 0`，console 零错误（Vue Flow 加 shim 之后）。

**Demo（1600×900）**：Vue Flow 和 React Flow 结果相同。初始 4 条边，用鼠标拖出 E→F 和 F→D 之后变成 6 条：A→B, B→C, C→A, D→E, E→F, F→D。回边的样式是 `stroke-dasharray: 6px, 4px`，标签是「第 3 轮 / 上限 10」。LiteGraph 同样连出了 6 条。
截图：`spike/shots/vueflow-demo.png`、`vueflow-demo-after-connect.png`、`reactflow-demo.png`、`reactflow-demo-after-connect.png`、`litegraph-demo.png`、`litegraph-demo-after-connect.png`。压测截图在 `shots/*-stress-*.png`。

**压测（分阶段，ms；长任务记为「时间点@阶段:时长」）**

| 运行 | 平移 p95 / max | 只切状态 p95 / max | 两者同时 p95 / max | 长任务 |
|---|---|---|---|---|
| Vue Flow 300 data | 16.7 / 133 | 16.8 / 16.8 | 16.8 / 167 | 17@pan:136, 4049@both:165, 6833@both:170 |
| Vue Flow 500 data | 16.7 / 283 | 16.8 / 33.3 | 33.4 / 300 | 14@pan:297, 4068@both:293, 6831@both:257 |
| Vue Flow 500 map（独立状态表） | 16.8 / 250 | 16.7 / 16.8 | 16.7 / 267 | 8:262, 4041:272, 6824:251 |
| Vue Flow 500 data vis=1 | 33.3 / 100 | 16.7 / 16.8 | 33.4 / 117 | 2:106, 4052:124, 4176:59, 6835:67 |
| **Vue Flow 500 data lod=keep** | 16.8 / 16.8 | 16.7 / 16.8 | 16.7 / 33.4 | **0** |
| React Flow 500 data（updateNodeData） | 16.8 / 67 | 16.7 / 16.8 | 16.8 / 50 | 15:75, 4049:52, 6832:60 |
| React Flow 500 map（独立 store） | 16.8 / 67 | 16.7 / 16.8 | 16.7 / 50 | 14:79, 4047:63, 6830:58 |
| React Flow 500 array（setNodes） | 16.7 / 67 | 16.7 / 16.8 | 16.8 / 100 | 14:72, 4051:98, 6831:57 |
| React Flow 500 data vis=1 | 16.8 / 16.8 | 16.8 / 16.8 | 16.7 / 16.8 | 0 |
| React Flow 500 data lod=keep | 16.8 / 16.8 | 16.8 / 16.8 | 16.7 / 16.8 | 0 |
| LiteGraph 500（首轮协议，不分阶段） | 整段 p95 16.7，max 50.1 | | | 0 |

全部数据在 `spike/results.json`（`phases` 字段）。

**Vue Flow 的长任务出现在什么时候？** 不在挂载时（已经等了 2 s），也不在切状态时（「只切状态」阶段一个都没有），而是在缩放**跨过 LOD 阈值 0.6 的那一帧**。按脚本里的 zoom 公式算，三次长任务的时间点分别对应：起步时从 fitView 的缩放跳到 0.625，约 4.05 s 时降到 0.54，约 6.83 s 时回到 0.60。spike 的 LOD 用 `display:none` 隐藏标签，节点尺寸跟着变，库就把 500 个节点全部重新测量一遍。改成 `visibility:hidden`（`?lod=keep`）之后，500 节点的长任务为 0。React Flow 在同样场景下的长任务只有 52–98 ms。结论：**LOD 只能隐藏，不能改变节点尺寸。**

**注意**：headless Chrome 用软件 GL，帧率锁在 60 Hz 附近，所以 16.7 ms 是下限。数字只能拿来比较几个库，不能当作 Alex 机器上的真实值。

**同一个状态节点，两种写法**

| | 文件 | 行数 |
|---|---|---|
| Vue（模板字符串） | `spike/vueflow/StatusNode.js`（加上 `statusStore.js` 3 行） | 18 |
| React（htm） | `spike/reactflow/StatusNode.js`（加上 `statusStore.js` 8 行、`html.js` 3 行） | 16 |
| 回边 LoopEdge | `spike/vueflow/LoopEdge.js` 22 行 / `spike/reactflow/LoopEdge.js` 14 行 | |
| LiteGraph 同样的节点和回边 | `spike/litegraph/app.js`，其中 renderLink 重写约 20 行，手写中文换行 | |

**vendored 文件清单**：`/workspace/agent-ui/spike/vendor-manifest.json`。每个文件都记了包名、版本、来源 URL、转换方式和 sha256。
- Vue Flow：6 个文件，627,597 B，npm 原文件，没有转换。
- React Flow：13 个文件，411,384 B，经 esm.sh 转码，再由 vendor_fetch.py 改写 import 路径。
- LiteGraph：2 个文件，630,910 B，npm 原文件。

**发现的问题**
- Vue Flow 必须加 process shim。
- 早先 Vue Flow 截图一片空白，原因是 F 的端口被 minimap 盖住，拖动变成了平移视图。这是测试环境的问题，改成 1600×900 后已解决。
- React Flow 第一次 vendor 时漏了相对路径的 import，已修复。

---

## 7. Canvas 和 DOM 怎么取舍

- **DOM（Vue Flow、React Flow）**：文字、中文换行、tooltip、选中复制、icon 加文字的错误态、无障碍，都是浏览器自带的。代价是节点多了以后要管好重排：不要在 LOD 时改尺寸，状态更新不要整体替换数组。实测 500 个节点没问题。
- **Canvas（LiteGraph）**：500 个节点轻松跑，缩放降级是内置的。但每一个文字和交互细节都要自己画、自己做点击检测（`fillText` 不换行，见 [MDN](https://developer.mozilla.org/en-US/docs/Web/API/CanvasRenderingContext2D/fillText)）。PRD 要求节点用 DOM 渲染，所以排除。
- 推断：如果以后要上几千个节点，再考虑 DOM 节点加 canvas 缩略层的混合方案。现在不需要。

---

## 8. 还没定的问题

1. 撤销和复制粘贴自己写，还是先不做？（Vue Flow 两样都没有。）
2. dagre 之类的自动布局库是否也要 vendor？要先确认它有能直接在浏览器里跑的 ESM/UMD 文件（还没查）。
3. Vue Flow 的发版节奏（上一版 2026-01-28）能不能接受？如果不能，退路是 React Flow 加 esm.sh vendoring。
4. PRD 第 19 条（旧称 8.13）的数字要在 Alex 的机器上用真 GPU 复测后再定。
5. 「Vue 还是 React」：推断，跟 vue-vet 保持同栈更省事。只有在团队更熟 React，或者需要 Pro 支持时，才换 React Flow。
