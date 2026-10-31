<img src="assets/logo.svg" alt="Roboact" width="96">

# Roboact

离线、零构建的机器人流程画布：把适配器交出的原子动作连成可循环的流程，用断点、单步、人工确认和运行记录调试。画布编排的是具身智能体（机器人），不是聊天 LLM。

- 后端：Python asyncio。运行记录默认 SQLite（`Store` 接口；测试可用内存实现）
- 前端：Vue 3 + Vue Flow 1.48.2，**零构建**（原生 ES 模块 + import map）
- 适配器：同一套接口挡住 SDK / ROS 2。v1 只带 `FakeAdapter`（`target=sim`）
- **离线**：前端、图标、字体在仓库里；Python 依赖用自带 wheel

规格：`docs/prd-v1.md`（v1.4.1）、`docs/exec-model-v0.md`（v0.2）、`docs/ui-spec-v0.md`（v0.2）、`docs/system-design-v0.md`（v0.2）。

本仓库不发布到 PyPI，也不带发布配置。

## 离线安装并启动

需要 **CPython 3.12、linux x86_64**（仓库里的 `pydantic-core` wheel 按这个编的）。

```bash
pip install --no-index --find-links vendor/wheels -r requirements.txt && python -m roboact
```

浏览器打开 <http://127.0.0.1:7465>。不需要 Node，没有 `npm`，没有打包步骤。

```bash
python -m roboact --host 127.0.0.1 --port 7465 --db data/roboact.db
```

默认绑 `127.0.0.1`。`--token` **没有默认值**（代码、示例和文档里都不写死令牌）。若 `--host` 不是本机回环，必须带 `--token`，否则进程拒绝启动。也可把令牌放进环境变量 `ROBOACT_TOKEN`（同样没有内置值）。每个 HTTP/SSE 请求用 `X-Roboact-Token` / `Authorization: Bearer` / `?token=` 带上，否则 401。401 时界面只显示「访问令牌」和「连接」，没有账号登录页。

默认：总步数上限 **1000**，节点超时 60 s（作为绝对 deadline 传给适配器；Python 侧再守一次），适配器心跳 3 s，页面断开暂停 10 s，SSE 重连 2 s，SQLite `busy_timeout` 5000 ms。

## 怎么用

1. 打开是一张无限画布。左侧按适配器名分组列出动作，加上开始、输出、条件、合并、人工确认、常量（没有循环节点；环用回边加条件）。
2. 顶栏是流程名、适配器名；只有 `target=real` 时才显示红框「真机」。没有仿真/真机切换。
3. 连线时类型不同的端口会拦住。参数在右侧。节点左缘执行前断点，右缘执行后断点。
4. 顶栏三个按钮位固定不挪：**运行 / 暂停 / 继续**、**单步**、**停止**。运行中第一格是「暂停」，第三格「停止」可用。点暂停后先显示「暂停中…」，收到 `paused` 再变成「继续」。**F5** 运行/继续，**F6** 暂停，**F10** 单步，**Shift+F5** 停止。停止不是急停。
5. 运行中的节点：有进度显示「运行中 · 40%」或最新反馈；没有则显示「运行中 · 12 s」。图像只出现在右侧输出页，不进画布缩略图，日志只记尺寸/时间/字节。
6. 适配器动作列表版本写进流程。对不上就拦运行；动作没了标「动作已不存在」。缺必填说明字段、或适配器报了 `side_effect=write` 的动作不生成节点，面板列出缺的字段。右侧对 `expiry=adapter_only` 显示「到期只靠 adapter」。
7. 停止不可取消的阻塞动作：等它做完，运行日志记 `not cancelled`。取消被拒：节点继续跑，显示「取消被拒」。
8. 服务重启后运行保持暂停。崩溃时正在执行的 `motion` 节点是「中断 · 结果未知」，**不会自动重跑**。先读回机器人状态，再选「重做这一步」或「当作已做完」。测试用 `write` 节点继续时带原幂等键重试。
9. 适配器 3 s 不可达 → 已暂停 · 机器人控制器断开（红，「继续」要等适配器回来）。后端心跳停了，适配器基类自己取消在飞目标。页面 SSE 10 s 没人连 → 已暂停 · 页面断开（琥珀，重连后可继续；不取消正在跑的节点）。

副作用取值：`none | read | motion | write`。`motion` 是会让机器人动的动作（PRD v1.4.1 与 exec-model v0.2 一致；缺省按 `motion`）。`write` 只给手写测试节点（`append_note` / `demo_notes.db`），不进产品节点面板。

## 测

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium   # 若本机没有 Chrome
pytest -q
```

CI 会重算 `vendor-manifest.json` 里每个文件的 sha256，并且要求每条都有 SPDX `license` 和 `vendor/licenses/` 下的原文，缺了就不让过。

`append_note` / `demo_notes.db` 不在产品路径上，只作为执行器/幂等测试夹具（A18、A20）。产品写副作用走 FakeAdapter 内存。

## 验收状态

只把**浏览器里对着真实后端**测过的项标成通过。只在服务端队列或 `SseHub` 上量过的，不算通过。

| 项 | 状态 | 依据 |
|---|---|---|
| A4 / PRD 19.1 推送合并 | **pass** | `test_a4_sse_batching_500_nodes_browser`：Chromium + 真实 SSE，500 节点跑完，批次/秒 ≤ 10，画布终态与后端一致。服务端 `test_batch_rate_at_most_10_per_second` 只作对照，不单独算通过。 |
| A5 / PRD 19.2 暂停延迟 | **pass** | `test_a5_pause_latency_frames_in_browser`：500 节点运行中点暂停，从点击起数 `requestAnimationFrame` 到顶栏「已暂停」，5 次均 ≤ 20 帧。服务端 `test_pause_event_immediate_a5` 不单独算通过。 |
| A6 重连 + 页面断开 | **pass** | `test_a6_reconnect_and_half_open_page_lost`：TCP 代理真切断后重连，事件序号不回退、画布对齐后端；半开（停转发、不发 FIN/RST）后后端以 `operator_lost` 暂停，半开后不再派发新的 motion。服务端 `replay_after` / A33 不单独算通过。 |
| PRD 19 画布性能 | **pass** | 浏览器 `test_prd19_canvas_performance_500_nodes`（平移/状态洪水 p95 ≤ 33 ms）加上 A4 的真实 SSE 500 节点。 |
| PRD 19.2 停下不等窗口 | **pass** | 同 A5：`test_a5_pause_latency_frames_in_browser`。 |

## 仓库怎么摆

```
roboact/            Python 包（适配器、节点注册表、图、执行器、Store、HTTP+SSE）
web/                静态前端，由后端直接托管
web/vendor/         Vue / Vue Flow / JetBrains Mono / 图标（未改动的原文件）
assets/logo.svg     品牌标志（favicon 同源）
vendor/wheels/      运行时 Python wheel
vendor/licenses/    第三方许可原文
vendor-manifest.json
tests/
scripts/check_manifest.py
```
