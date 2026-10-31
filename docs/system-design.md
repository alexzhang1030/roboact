# 系统设计图 v0：Agent 流程画布

日期：2026-10-31（v0.2：跟随 exec-model v0.2 改了图 1–4，后端搬到机器人控制器上，加 adapter、动作节点、取消、到期和心跳）　依据：执行模型规格 `exec-model-v0.md` v0.2（以它为准）、PRD v1.4 定稿、界面规格 v0（含第 16 节，2026-10-31 17:05 读取）、Torvalds 17:14 已实现的默认值
图的源码就是下面的 mermaid 代码块；PNG 在 `/workspace/agent-ui/diagrams/`，由 mermaid-cli（`mmdc`）从同一份代码渲染。
所有状态名、端点名、事件名照抄 `exec-model-v0.md`。和界面规格、PRD 命名不同的地方列在文末。

图里的线分四种：

| 线型 | 含义 |
|---|---|
| 蓝色实线，标「HTTP」 | 浏览器发出的普通 HTTP 请求：取页面和依赖文件，或者发指令 |
| 绿色虚线，标「SSE」 | 服务端往浏览器推的事件流。SSE（Server-Sent Events）是一条不关闭的 HTTP 响应，服务端在里面一条一条写事件，浏览器用自带的 `EventSource` 接 |
| 灰色粗线，标「本地写盘」或「本地读」 | 后端进程读写控制器上的文件 |
| 橙色实线 | 后端和 adapter、adapter 和控制程序之间：发目标、收反馈、取消、心跳 |

---

## 图 1：部署和组件

这张图回答：系统由哪些部分组成，各放在哪里，彼此之间走什么协议，哪里会断。

后端、adapter、控制程序都在机器人控制器上（PRD 第 7 节）。adapter 分两半：后端进程里的客户端，和单独进程的守护进程；后端死了，守护进程收不到心跳，就取消还在执行的目标（`exec-model-v0.md` 第 2.5、2.5.4 节）。急停走硬件，不经过这张图里的任何软件。

![部署和组件](diagrams/1-deployment.png)

```mermaid
flowchart LR
  subgraph BRH["浏览器所在的电脑（在哪待 Alex 定）"]
    subgraph BR["浏览器"]
      direction TB
      VF["Vue Flow 1.48.2 画布<br/>节点状态、断点、入口端口<br/>流程名右边：adapter 名字 + 红框「真机」（读 target）"]
      ES["EventSource<br/>断线自动重连，带 Last-Event-ID"]
      UI["顶栏、右侧面板<br/>运行 / 暂停 / 继续 / 单步 / 停止<br/>输出标签里看相机图像"]
      ES -->|"nodes 事件在下一帧一次改完"| VF
      UI --- VF
    end
  end

  subgraph CTRL["机器人控制器（四足 + 机械臂 + 腕部相机；是不是 Jetson Thor 待确认）"]
    direction LR
    subgraph PY["Python 后端（一个进程，asyncio；普通优先级，不占控制循环的 CPU 核，内存设上限）"]
      direction TB
      API["HTTP 指令端点 /api/...<br/>默认只绑 127.0.0.1；--host 绑别的地址要令牌，否则 401"]
      REG["节点注册表<br/>控制节点 + 动作节点（每个动作一个）<br/>+ 仅测试节点"]
      SCH["调度器（每次运行一个协程）<br/>断点、步数上限<br/>每次调用把到期时间传给 adapter<br/>Python 60 s 第二道超时"]
      PUSH["SSE 推送层<br/>每 100 ms 合并一条 nodes 事件<br/>没有页面连着超过 10 s → 暂停"]
      AC["adapter 客户端<br/>list_actions / send_goal / feedback<br/>result / cancel / read_state<br/>每 3 s 内没回探测 → 暂停"]
      WR["写连接线程<br/>所有 SQLite 写入走这里"]
      IMG["图像缓存（只在内存）"]
      TOOLS["仅测试节点<br/>read_note / append_note / clock"]
      API -->|"指令"| SCH
      REG -->|"校验输入、输出、参数"| SCH
      SCH -->|"状态变更"| PUSH
      SCH -->|"T1 / T2 事务"| WR
      SCH -->|"动作节点"| AC
      SCH -->|"测试节点"| TOOLS
      AC -->|"相机图像"| IMG
    end

    AD["adapter 守护进程（单独进程）<br/>报 name、target、动作清单 version<br/>心跳断了 → 取消还在执行的目标"]
    RC["控制程序（ROS 2 动作服务端或厂商 SDK）<br/>控制循环，实时线程<br/>控制器自己的到期时间（如 Spot end_time）"]

    subgraph DISK["控制器磁盘（数据目录）"]
      direction TB
      DB[("runs.db（SQLite，WAL）<br/>flow_versions（含动作清单版本）、runs、steps<br/>pending、changes、breakpoints、pins、commands<br/>图像只记元数据")]
      NOTES[("demo_notes.db（仅测试）<br/>note_lines + idem_keys 同一事务")]
    end
  end

  EST["硬件急停<br/>不经过网页、后端、adapter"]

  VF ==>|"HTTP GET 页面、依赖、/api/node-types<br/>127.0.0.1，或 SSH -L 转发"| API
  UI ==>|"HTTP 指令<br/>127.0.0.1，或 SSH -L 转发"| API
  PUSH -.->|"SSE text/event-stream<br/>GET /api/runs/{run_id}/events"| ES
  AC -->|"目标、取消、心跳、探测"| AD
  AD -->|"send_goal / cancel / 读状态"| RC
  WR ---|"本地写盘"| DB
  PUSH ---|"本地读（断线补发读 changes）"| DB
  TOOLS ---|"本地读写（仅测试）"| NOTES
  EST -->|"硬件通路"| RC

  classDef box fill:#FFFFFF,stroke:#8A8A84,color:#1A1A18
  classDef robot fill:#FFF4D6,stroke:#9A6700,color:#1A1A18
  classDef stop fill:#FDECEC,stroke:#B42318,color:#1A1A18
  class VF,ES,UI,API,REG,SCH,PUSH,AC,WR,IMG,TOOLS,DB,NOTES box
  class AD,RC robot
  class EST stop
  linkStyle 9,10 stroke:#1F6FEB,stroke-width:2px
  linkStyle 11 stroke:#1A7F37,stroke-width:2px,stroke-dasharray:6 4
  linkStyle 12,13 stroke:#C2410C,stroke-width:2px
  linkStyle 14,15,16 stroke:#5C5C57,stroke-width:3px
  linkStyle 17 stroke:#B42318,stroke-width:3px
```

要点：
- 哪里会断：后端死了，ROS 2 动作服务端发现不了（[rclcpp#2241](https://github.com/ros2/rclcpp/issues/2241)），所以靠守护进程的心跳取消目标；守护进程也死了，靠控制器自己的到期时间；控制器不接受到期时间的动作（「到期只靠 adapter」），后端卡住时这一步停不下来，界面在参数表单底部写明（界面规格 16.y）。
- 10 s、3 s、60 s 是 Torvalds 已实现的默认值，没有出处，可配置。
- 浏览器默认在控制器本机打开；在另一台电脑上时，用 SSH 端口转发，或者 `--host` 绑局域网地址加令牌。待 Alex 定（`exec-model-v0.md` 第 0.2 节）。
- 图像只在内存里，`runs.db` 里只有尺寸、拍摄时间、第几轮、第几步。`demo_notes.db` 只给测试节点用。

---

## 图 2：一次运行的时序

这张图回答：从点「运行」到运行结束，浏览器、后端、adapter、控制程序之间按什么顺序发生什么；停止、动作超时和断开时怎么走。

![一次运行的时序](diagrams/2-run-sequence.png)

```mermaid
sequenceDiagram
  autonumber
  actor U as 用户
  participant B as 浏览器
  participant A as HTTP 指令端点
  participant S as 调度器
  participant D as runs.db
  participant AD as adapter（客户端 + 守护进程）
  participant R as 控制程序

  U->>B: 点「运行」
  B->>A: POST /api/runs（flow_id, flow_version, client_id）
  A->>D: 读 flow_versions，锁定这一版（含动作清单版本、动作说明哈希）
  A->>AD: 当前动作清单 version 和各动作说明
  alt adapter 没连上
    A-->>B: 503 adapter_unavailable
  else 动作不在了或定义变了
    A-->>B: 409 action_missing / action_changed（节点显示「动作已不存在」）
  else 环没有出口
    A-->>B: 400 loop_without_exit
  else 检查通过
    A->>D: 写 runs（status=running, adapter_name, adapter_target）和开始节点的 pending
    A->>S: 启动这次运行的协程
    A-->>B: 201 run_id, last_event_id
  end
  B->>A: GET /api/runs/{run_id}，再订阅 events?after=

  loop 每一步：有就绪节点，且步数没到上限
    S->>D: T1：写 steps（status=running），pending 标已消费
    alt 动作节点（motion）
      S->>AD: send_goal（动作、参数、到期时间 expires_at）
      AD->>R: 发目标，到期时间换成控制器参数
      AD-->>S: goal_id（写进 steps）
      R-->>AD: 反馈（goal_progress 动作）
      AD-->>S: feedback → 节点「运行中 · 40%」或「运行中 · 12 s」
      R-->>AD: 结果 + 结束时实测状态
      AD-->>S: result
    else 控制、测试节点
      S->>S: run(inputs, params, ctx)
    end
    S->>D: T2：写输出和状态，给每条出线写 pending，写 changes
    S-->>B: SSE nodes（每 100 ms 最多一条）
  end

  Note over S: 断点只在节点之间：执行前断点在 T1 之前，执行后断点在 T2 之后
  S->>D: runs.status=paused, pause_json（pause_id, reason=breakpoint, robot_state）
  S-->>B: SSE nodes，紧跟 SSE paused
  U->>B: 点「继续」
  B->>A: POST /api/runs/{run_id}/continue（pause_id）
  A-->>B: 200，SSE resumed

  Note over U,R: 停止：动作在执行中
  U->>B: 点「停止」（停在原地，电机不断电）
  B->>A: POST /api/runs/{run_id}/stop
  A-->>B: 202 stop_requested=true
  S->>AD: cancel(goal_id)
  AD->>R: 取消目标
  alt 取消被接受
    R-->>AD: 目标 CANCELED，读回状态
    S->>D: steps.status=stopped，cancel_json，runs.status=stopped
  else 取消被拒
    R-->>AD: REJECTED
    S-->>B: SSE cancel_result → 节点「运行中 · 取消被拒」，顶栏「停止被拒」
    Note over S,R: 不再派发节点；动作自己结束后按结果记，运行 stopped
  else 没回应或读不到状态
    S->>D: steps.status=interrupted_unknown，runs.status=stopped
  end

  Note over S,R: 动作超时：控制器先到期 → error（expired）；60 s 第二道到点 → 调取消、读状态
  S->>D: steps.status=interrupted_unknown，runs.status=paused（reason=motion_timeout）
  B->>A: POST /api/runs/{run_id}/continue（没带 readback_id）
  A-->>B: 409 readback_required
  B->>A: POST /api/runs/{run_id}/readback → readback_id
  B->>A: continue（pause_id, readback_id, resolution=retry 或 mark_done）

  Note over B,S: 没有页面连着超过 10 s → 当前节点做完后暂停（operator_lost，「已暂停 · 页面断开」）
  Note over S,AD: 探测 adapter 3 s 没回应 → 暂停（adapter_lost，「已暂停 · 机器人控制器断开」）
  S-->>B: SSE run_finished（completed / error / stopped / step_limit）
```

要点：
- 暂停和断点不取消正在执行的动作，等它做完才停（`exec-model-v0.md` 第 2.6、3 节）。先返回的动作（`goal_progress`）也要等结果回来才算做完。
- 结果未知的动作永不自动重试：继续前必须有一次读回，再由人选「重做」（新步号，`retry_of` 指向原步）或「当作已做完」（手填输出）。
- 测试用 `write` 节点超时照 v0.1：记 `interrupted_unknown`，运行 `paused`（`reason=write_timeout`），继续时带原幂等键重试。

---

## 图 3：节点状态和运行状态

这张图回答：一个节点、一次运行各有哪些状态，从一个状态到另一个状态靠什么触发。节点状态是 SSE `nodes` 事件里的 `state`；运行状态是 `runs.status`，暂停时另有暂停原因 `reason`。

![节点状态和运行状态](diagrams/3-state-machines.png)

```mermaid
stateDiagram-v2
  direction LR
  state "节点状态（nodes 事件的 state）" as NODE {
    state "idle 空闲" as idle
    state "queued 排队" as queued
    state "paused_before 已暂停 · 执行前" as paused_before
    state "running 运行中（动作节点可带 · 取消被拒）" as running
    state "awaiting_approval 等待确认" as awaiting_approval
    state "paused_after 已暂停 · 执行后" as paused_after
    state "done 完成" as done
    state "error 出错" as error
    state "skipped 跳过" as skipped
    state "stopped 已停止" as stopped
    state "interrupted 中断" as interrupted
    state "interrupted_unknown 中断 · 结果未知" as interrupted_unknown

    [*] --> idle
    idle --> queued: 必填端口都有待执行数据
    queued --> running: 调度器派发，T1 提交
    queued --> paused_before: 执行前断点命中 / 各种暂停
    paused_before --> running: continue 或 step
    running --> done: T2 提交
    running --> paused_after: T2 提交，执行后断点命中
    paused_after --> done: continue 或 step
    running --> awaiting_approval: 人工确认节点
    awaiting_approval --> done: approve
    running --> error: ① 出错
    running --> stopped: ② 停止成功
    running --> running: ③ 取消被拒
    running --> interrupted_unknown: ④ 结果未知
    running --> interrupted: 崩溃
    interrupted --> running: continue 重跑
    interrupted_unknown --> queued: ⑤ retry，重新排队
    interrupted_unknown --> done: ⑥ mark_done
    done --> queued: 环里下一轮数据到齐，round 加一
    idle --> skipped: 选了另一路，且只能从没选那一路到达
  }

  state "运行状态（runs.status）" as RUN {
    state "running 运行中" as r_running
    state "paused 已暂停（reason）" as r_paused
    state "completed 完成" as r_completed
    state "error 出错" as r_error
    state "step_limit 超出步数上限" as r_limit
    state "stopped 已停止" as r_stopped

    [*] --> r_running: POST /api/runs 返回 201
    r_running --> r_paused: ⑦ 暂停
    r_paused --> r_running: ⑧ 放行
    r_paused --> r_paused: 重启 → restart
    r_running --> r_completed: 没有就绪节点
    r_running --> r_error: 节点 error
    r_running --> r_limit: steps_used + 1 大于 max_steps
    r_running --> r_stopped: stop
    r_paused --> r_stopped: stop
    r_completed --> [*]
    r_error --> [*]
    r_limit --> [*]
    r_stopped --> [*]
  }
```

图里带圈的编号：

| 编号 | 触发 |
|---|---|
| ① 出错 | 抛异常；`none`、`read` 节点超时；动作被控制器拒绝、中止、到期；前提条件不满足；输出不能序列化 |
| ② 停止成功 | 点停止：`none`、`read` 节点取消任务；动作节点取消被接受并读回 |
| ③ 取消被拒 | 点停止，控制器拒绝取消：节点保持运行中，等动作自己结束 |
| ④ 结果未知 | 动作：60 s 第二道超时、adapter 断开、服务崩溃、取消没回应；测试 `write` 节点：超时或崩溃 |
| ⑤ retry | 动作：读回后人选重做，重新排队，新步号；`write`：继续时重新派发，沿用原幂等键 |
| ⑥ mark_done | 动作：读回后人选当作已做完，手填输出 |
| ⑦ 暂停 | `reason` 取 breakpoint / user / step_done / approval / storage_error / write_timeout / motion_timeout / adapter_lost（探测 3 s 没回应）/ operator_lost（没有页面连着 10 s） |
| 崩溃 → interrupted | `none`、`read` 节点执行中服务崩溃；continue 时用新步号重跑 |
| ⑧ 放行 | continue、step 或 approve（approval 时只认 approve）；结果未知时要 `readback_id` 和 `resolution` |

要点：
- 「取消被拒」不是单独的状态：节点仍是 `running`，带 `cancel.cancel_response = rejected`；运行仍是 `running`，带 `stop_requested = true`，界面据此写「停止被拒」。
- 「动作已不存在」也不在状态机里，它是开跑前的检查结果，运行根本不会开始。
- `continue`、`step`、`approve` 都要带当前的 `pause_id`，不一致返回 409 `pause_mismatch`；停在人工确认上时 `continue`、`step` 返回 409 `awaiting_approval`；结果未知时没带读回返回 409 `readback_required`。这几条自环图里没画。
- 动作节点的输出不能在执行后断点上改（422 `motion_output_readonly`）。

---

## 图 4：服务重启恢复

这张图回答：动作或测试写节点执行到一半服务崩溃，重启后系统怎么判断，界面显示什么，为什么不会自动再做一遍。

记录在 SQLite 里，重启后都在。执行器只知道某一步 T1 提交了、T2 没提交。动作节点：先问控制器这个目标现在怎样（动作服务端缓存结果，状态话题是 TRANSIENT_LOCAL，见 [ROS 2 Actions 设计文档](https://design.ros2.org/articles/actions.html)），再读一次机器人状态，然后停住等人。测试写节点：照 v0.1，继续时带原幂等键重试。

![服务重启恢复](diagrams/4-restart-recovery.png)

```mermaid
flowchart TB
  CR["服务崩溃<br/>心跳停 → adapter 守护进程取消还在执行的目标<br/>守护进程也没了 → 控制器到期时间兜底"] --> R["服务重启，先连 adapter"]
  R --> S1["扫 runs：status 是 running 或 paused 的运行"]
  S1 --> S2["读 pending 未消费的行 → 重建待执行队列<br/>读 steps → 各节点轮次、steps_used"]
  S2 --> S3{"有 status=running 的记录吗<br/>（T1 提交了、T2 没提交）"}
  S3 -->|"没有"| P
  S3 -->|"有，none 或 read"| I1["记 interrupted<br/>pending 恢复成未消费<br/>继续时自动重跑"]
  S3 -->|"有，motion（动作）"| M1["记 interrupted_unknown<br/>pending 恢复成未消费，不自动重发"]
  S3 -->|"有，write（仅测试）"| I2["记 interrupted_unknown<br/>pending.retry_idem_key = 原 idem_key"]
  M1 --> M2["adapter.active_goals()：按 goal_id 问控制器<br/>还在执行 / 已成功 / 已取消 / 已中止 / 查不到<br/>写进 pause.goal_check"]
  M2 --> M3["adapter 读一次机器人状态<br/>（夹爪是否夹着东西单独一行）<br/>写进 pause.readback"]
  I1 --> P
  M3 --> P
  I2 --> P
  P["runs.status = paused，reason = restart<br/>停在下一个要派发的节点执行前"]
  P --> Q["界面：运行记录都在<br/>顶栏「已暂停 · 服务重启后」<br/>动作节点「中断 · 结果未知」<br/>右侧面板：发出的目标、最后反馈、控制器那边的状态、现读的状态"]
  Q --> W{"用户操作"}
  W -->|"停止"| STP["runs.status = stopped"]
  W -->|"动作节点：重新读取 → readback_id<br/>确认看过机器人"| RS{"resolution"}
  RS -->|"retry"| RT["重做：新步号，retry_of = 原步号"]
  RS -->|"mark_done"| MD["当作已做完：手填输出"]
  W -->|"write 节点：继续（pause_id）"| WT["重新派发，idem_key 沿用 run_id:原步号<br/>append_note 查台账：有就返回上次结果，没有才写"]
  W -->|"没带 readback_id 就继续"| E409["409 readback_required，运行不动"]
  RT --> T2["T2 提交，给下游写 pending，往下跑"]
  MD --> T2
  WT --> T2

  classDef warn fill:#FFF4D6,stroke:#9A6700,color:#1A1A18
  classDef ok fill:#FFFFFF,stroke:#8A8A84,color:#1A1A18
  classDef bad fill:#FDECEC,stroke:#B42318,color:#1A1A18
  class M1,M2,M3,I2,P,Q warn
  class CR,R,S1,S2,I1,RT,MD,WT,STP,T2 ok
  class E409 bad
```

要点：
- 普通的继续和单步在这里不可用，因为它们会原样重发同一个动作；只有读回之后人选「重做」或「当作已做完」（`exec-model-v0.md` 第 2.6、8 节，验收 A29）。
- 控制器那边查到目标「还在执行」，说明心跳这一层没起作用，界面要明说。
- adapter 连不上时，恢复照做，`goal_check` 和 `readback` 写「adapter 未连上」，继续和单步置灰。
- 测试写节点这条路和 v0.1 一样（验收 A1、A18、A20）。

---

## 渲染方法

```
cd /workspace/agent-ui/diagrams
mmdc -i 1-deployment.mmd -o 1-deployment.png -c mermaid-config.json -p puppeteer-config.json -w 2000 -s 2 -b white
```

四张图同样渲染，竖长的图用 `-s 3` 让宽度过 1600px。`puppeteer-config.json` 指向本机的 `/usr/bin/google-chrome`。`mermaid-config.json` 把字体设为 `Noto Sans CJK SC`（本机已装），中文不会变成方块。`.mmd` 文件是从本文件的代码块原样抽出来的。

---

## 和 exec-model、PRD、界面规格对不上的地方

图里一律用 `exec-model-v0.md` v0.2 的名字。PRD 和界面规格我都没改。完整清单在 `exec-model-v0.md` 文末，这里只列和图直接相关的：

1. 界面规格第 1 节状态词还没有「页面断开」「机器人控制器断开」和动作超时的词；图 2、图 3 用的是 Torvalds 已实现的「已暂停 · 页面断开」「已暂停 · 机器人控制器断开」。
2. 界面规格第 3.1 节说 `interrupted_unknown` 只出现在 `write` 节点，`interrupted` 继续时自动重跑。图 3、图 4 里动作节点被打断记 `interrupted_unknown`，要读回加人确认。
3. PRD 第 3 节第 14 步说关掉页面运行照常继续；现在没有页面连着超过 10 s 会暂停。
4. 界面规格第 16 节「停止被拒」在运行状态里没有对应值，图 3 里是 `running` 加 `stop_requested`。
5. 界面规格第 1 节的「暂停中…」仍是浏览器自己的显示状态，没画进运行状态机（和 v0.1 一样）。
