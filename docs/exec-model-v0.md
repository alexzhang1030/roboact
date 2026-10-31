# 执行模型规格 v0：Agent 流程画布的 Python 执行器

日期：2026-10-31　状态：草稿 v0.2（2026-10-31 按 Alex、Steve、Tedrake 的决定改成驱动机器人：节点由 adapter 从控制器的原子动作生成，新加副作用类别 `motion`、adapter 一节、动作说明字段表、`return_mode` 两种方式下暂停和停止怎么做、取消被拒、停到期时间和心跳；存储和重启恢复照 v0.1 不变），待 Torvalds 评审实现细节、待 Alex 定第 14 节两个决策和第 16 节的未决项
读者：写 Python 后端和 adapter 的 Torvalds；一起定动作说明字段的 Tedrake；把决策交给 Alex 的 CTO
依据：PRD v1.4 定稿（`/workspace/prd/agent-flow-canvas-v1.md`，2026-10-31 17:05 读取，重点第 2、5、7、8、9、10 节）、界面规格 v0（`/workspace/agent-ui/ui-spec-v0.md`，17:05 读取，含第 1、3.1、16 节和 16.x、16.y）、具身执行规则笔记（`/workspace/agent-ui/embodied-exec-v0.md`，作为本规格的调研附录，出处都在那份笔记里，本规格引用时照抄链接）、画布库评估（`/workspace/agent-ui/flow-lib-eval-2026-10-31.md`）、依赖清单（`/workspace/agent-ui/spike/vendor-manifest.json`）。节点清单 v0（`/workspace/prd/node-list-v0.md`）PRD 已宣布作废，9 个节点只留着测执行器。
标记：「推断」表示我自己的估计或取舍，没有文件或官方出处；「临时」表示 CTO 先定、等 Alex 改的值；「待 Alex」表示取决于第 16 节的未决项。

这份规格只管服务端：怎么调度节点、怎么通过 adapter 让机器人做动作、怎么存记录、怎么停在断点、怎么恢复、怎么往浏览器推状态。界面怎么画以 Rams 的界面规格为准，这里只写服务端要给界面哪些字段。

## v0.2 先看这几条：接上机器人以后在哪里断

1. **流程进程死了，机器人不会自己停。** ROS 2 的动作服务端不知道客户端进程死了，目标会接着执行（[ros2/rclcpp#2241](https://github.com/ros2/rclcpp/issues/2241)，2026-10-31 读取时仍是 open）。所以每个会动机器人的动作都要有一个不靠流程进程的到期机制：控制器自己的到期时间，加上 adapter 收不到心跳时主动取消（第 2.5.4 节）。控制器没有到期参数的动作，后端卡死时就停不下来，说明里必须写明，界面要标出来。
2. **画布上的「停止」不是急停。** 停止 = 调用当前动作的取消，按 IEC 60204-1 的第 2 类停止理解：受控停下，电机保持供电。急停按 ISO 13850 走机器人自己的硬件通路，不经过网页、HTTP 和这个后端（第 5 节）。
3. **取消可能被拒。** rclpy 动作服务端默认的取消回调直接拒绝（`default_cancel_callback` 返回 `CancelResponse.REJECT`，文档字符串「No cancellations.」，[rclpy action/server.py](https://github.com/ros2/rclpy/blob/rolling/rclpy/rclpy/action/server.py)，2026-10-31 核对）。所以「能不能取消」由每个动作自己声明，每次取消的回应都要记下来，被拒时界面显示「取消被拒」。
4. **会动机器人的动作结果未知时不能重试。** 幂等键挡不住物理重复：机器人已经走了一半，再发同一个「向前走 1 m」，控制器不知道该补走多少（推断）。超时、停止没停成、服务崩溃以后，这一步一律记「中断 · 结果未知」，运行暂停；要继续，必须先由 adapter 读一次机器人当前状态，再由人确认。执行器永不自动重发动作（第 2.6 节）。
5. **控制器更新以后旧流程会跑不了。** 流程版本锁定时连动作清单的版本一起锁；开跑前对不上，第一个节点执行前就拒绝，找不到的动作显示「动作已不存在」（第 1、6 节）。

---

约束照抄 PRD 第 2 节：后端是 Python；前端零构建，浏览器直接加载 ES module，依赖都放进仓库离线加载；画布库是 Vue Flow 1.48.2；节点都是预先写好的，用户在画布里不能写 Python；流程可以带环；程序跑在机器人控制器上，adapter（对接机器人控制器的那层代码）交出控制器的原子动作（控制器能直接执行的最小动作）清单，每个原子动作生成一个节点类型。由此推出对后端的要求：推送只能用浏览器自带的 `EventSource`；节点参数表单和端口类型都从服务端导出的 JSON Schema 生成；所有执行逻辑都在服务端，浏览器只发指令、收状态；节点只到动作这一级，控制回路（以固定频率持续发指令的循环）留在控制器里，不做成节点（Alex 2026-10-31）。

运行环境：Python 3.11 及以上，因为要用 3.11 加入的 `asyncio.timeout()`（[文档](https://docs.python.org/3/library/asyncio-task.html#asyncio.timeout)）。运行记录用 Python 自带的 `sqlite3`（PRD 第 5 节：运行日志保留，第一期存 SQLite）。HTTP 框架没定，任何 ASGI 框架（Starlette、FastAPI 之类）都行（推断）。

### 0.1 后端跑在机器人控制器上：资源规则

PRD 第 7 节：后端跑在机器人控制器上，和机器人的控制程序在同一台机器上，会抢算力。规则（Tedrake，推断）：

- 后端用普通优先级跑（Linux 的默认调度策略 `SCHED_OTHER`，nice 值不调高），不申请实时优先级。
- 后端进程不绑到控制循环所在的 CPU 核上；哪几个核留给控制循环，由 Tedrake 按控制程序的配置定。
- 后端进程设内存上限（例如 systemd 的 `MemoryMax=` 或 cgroup），超了就被杀掉，不让它把控制程序挤出内存。具体值没有出处，等 Torvalds 实测占用后定（推断）。
- 控制线程永远不等后端：后端不在控制循环里，控制程序不同步调用后端，也不等后端的回应。adapter 和控制程序之间只用控制程序本来就有的接口（ROS 2 action、SDK 调用）。

这样后端卡住或崩了，只是流程停下，控制循环照常跑；动作本身怎么停，靠第 2.5.4 节的到期和心跳。

出处：ros2_control 的 controller_manager 文档写明，不管装的是什么内核，它的主线程都会尝试设置 `SCHED_FIFO`，优先级 50（「the main thread of Controller Manager attempts to configure SCHED_FIFO with a priority of 50」，[controller_manager 用户文档 · Determinism](https://control.ros.org/rolling/doc/ros2_control/controller_manager/doc/userdoc.html#determinism)，2026-10-31 核对）。实时内核的做法见 [ROS 2 文档：Building a real-time Linux kernel](https://docs.ros.org/en/jazzy/Tutorials/Miscellaneous/Building-Realtime-rt_preempt-kernel-for-ROS-2.html)（社区贡献的教程，以 Ubuntu 20.04 和 x86_64 为例）。所以控制循环如果是 ros2_control 一类，它跑在实时优先级上，普通优先级的后端抢不过它；这一条成立的前提是控制器装了实时内核或者至少允许设 `SCHED_FIFO`。Jetson Thor 上的 JetPack 7 有没有现成的实时内核，没有找到出处，待 Alex（第 16 节）。

### 0.2 监听地址

默认只绑定 `127.0.0.1`，同一局域网里的其他机器连不上。浏览器打开 `http://127.0.0.1:<端口>/`。启动参数 `--host` 可以改绑定地址；绑到任何非回环地址（不是 `127.0.0.1`、`::1`）时，服务端要求令牌，没带令牌或令牌不对的请求一律返回 401（Torvalds 2026-10-31 17:14 已实现）。

浏览器在哪还没定，待 Alex（第 16 节）：PRD 第 7 节写「浏览器在另一台电脑上，两者之间走网络」，但 CTO 2026-10-31 17:05 说这件事仍要问 Alex。

- 浏览器就在控制器上：照现在，绑 `127.0.0.1`。
- 浏览器在另一台电脑上：两种做法。一是 SSH 端口转发（`ssh -L 8000:127.0.0.1:8000 <控制器>`），服务端不改（推断）。二是 `--host` 绑局域网地址，带令牌访问，否则 401。令牌怎么生成、怎么交给浏览器，以 Torvalds 的实现为准；界面规格 16.x 说定了再画登录页。

---

## 1. 点「运行」

用户在顶栏点「运行」，浏览器发 `POST /api/runs`（字段见第 10 节）。服务端按下面的顺序处理，任何一步失败都不建运行，也不向机器人发任何东西：

1. 锁定版本。流程每保存一次就生成一个只读的版本（`flow_versions` 表一行，存整张图的 JSON 和它的 sha256，以及保存时的动作清单版本 `action_list_version` 和每个用到的动作的说明哈希，见第 6 节）。请求里带的 `flow_version` 必须存在。这次运行从头到尾只读这一版。运行中用户再改画布，只会生成新版本，不影响这次运行；服务端给这次运行的订阅者推一条 `version_drift` 事件，界面据此显示「画布已改，这次运行仍按 v12」的提示条（界面规格第 1 节）。
2. 检查 adapter。adapter 没连上，返回 503 `adapter_unavailable`（界面：「未连上机器人控制器」，运行按钮置灰，界面规格第 16 节）。连上了就让它重新取一次动作清单（第 2.5.1 节），和锁定版本里记的比：
   - 用到的动作在当前清单里找不到：返回 409 `action_missing`，`detail.nodes` 列出这些节点。界面在这些节点上显示「动作已不存在」（界面规格第 16 节，Rams 的词）。这是开跑前的检查结果，不是运行中的节点状态。
   - 清单版本变了、动作都还在，但有动作的说明哈希变了：返回 409 `action_changed`，`detail.nodes` 列出节点和变了的字段（推断的错误码）。用户要打开流程看一眼、保存出新版本再跑，新版本锁定新的清单版本。
   - 清单版本变了，但用到的动作说明都没变：照常开跑，记录里写两个版本号（推断：只因为别的动作变了就拒绝，用户会被频繁打断）。
3. 校验节点和连线。每个节点的类型必须在服务端已注册；参数用该节点类型的 Pydantic 参数模型校验（动作节点的范围和单位来自动作说明，第 2.5.1 节）；连线两端端口类型必须相同（PRD 第 5 节）。
4. 检查每个环都有出口。服务端在锁定的图上求强连通分量（一组节点里任意两个都能沿连线互相走到，就是一个强连通分量；节点数大于 1 或带自环的分量就是环）。每个环里必须至少有一个「条件判断」节点，并且它至少有一条出线连到环外。不满足就返回 400，错误码 `loop_without_exit`，带上这个环里的节点 id 列表（PRD 8 第 18 条）。保存流程时做同样的检查，只给警告不拒绝（PRD 5.1）。
5. 定步数上限。请求里可以带 `max_steps`；不带就用默认值 1000（临时，CTO 定，没有出处，等 Alex 定，见第 14 节 a）。步数指这次运行里所有节点执行次数的总和（PRD 5.1）。
6. 建运行。写 `runs` 表一行，状态 `running`，记下 adapter 报的 `name`、`target`（`sim` 仿真或 `real` 真机）和动作清单 `version`（第 2.5 节）；把「开始」节点的初始数据作为待执行数据写进 `pending` 表（见第 2 节），然后启动这次运行的执行协程。

没有「仿真 / 真机」开关：接的是哪个 adapter，就是哪个目标；假 adapter 就是仿真（Alex、Steve 2026-10-31，PRD 第 7 节）。v1 用假 adapter。

返回 201 和 `run_id`。浏览器拿到后先 `GET /api/runs/{run_id}` 拉一次完整状态，再用 `EventSource` 订阅 `/api/runs/{run_id}/events`（PRD 第 5 节「页面重连后先拉一次当前状态，再接着收推送」）。

---

## 2. 节点逐个执行

### 2.1 调度

每次运行有一个执行协程，它一次只派发一个节点，等这个节点执行完、结果落盘以后才派发下一个。动作节点「执行完」指 adapter 报了最终结果（成功、失败或已取消），不管这个动作是做完才返回还是马上返回（第 2.5.2 节）。节点之间只通过连线传数据：节点的 `run()` 拿不到图，也拿不到别的节点，不能直接调用下一个节点（PRD 第 5 节：否则断点停不住）。

有环的图没有拓扑序（拓扑序是把节点排成一列、每条线都从前指向后的顺序，带环时排不出来），所以调度不排序，只看待执行队列：

- 待执行数据（`pending` 表的一行）：一个节点执行完后，沿每条出线给下游的某个输入端口发一份数据，在被下游取走之前就是一条待执行数据。
- 就绪：一个节点的所有必填输入端口都至少有一条没被取走的待执行数据，这个节点就绪。可选端口在派发那一刻有数据就取，没有就用默认值（推断）。
- 每个端口按先到先取。一个节点同一端口积了多份数据时，一次执行只取最老的一份。
- 多个节点同时就绪时，先就绪的先跑；同时就绪的按节点在锁定版本里的顺序（`graph_json.nodes` 的下标）排。这样同一份输入跑两遍，执行顺序一样，服务重启前后的结果才能对上（PRD 8 第 11、18.1 条）。
- 没有就绪节点时，运行结束（第 9 节）。

一次只派发一个节点，所以同一时刻机器人最多在执行这次运行的一个动作（推断：并行动作，例如边走边抬臂，要由控制器做成一个原子动作，不在画布上并行）。

「合并」节点就是要求所有输入端口都有数据的节点，不需要特殊处理。「文本模板」的多路文本输入同理。

条件判断和人工确认这类有两路输出的节点，只往实际走的那一路发数据。规则是：输出模型里值为 `null` 的端口不发（推断）。

标「跳过」（`skipped`）：两路节点选定一路、T2 提交时，执行器在同一个事务里立刻把只能从另一路到达的节点标成跳过，不等运行结束（CTO 定；PRD 8 第 13 条「点「驳回」后……通过那一路的节点标「跳过」」）。在锁定版本的图上这样算：

1. 「选中的一路」能到达的节点集合 A：从选中端口连出的节点出发，沿连线（含回边）能走到的所有节点。
2. 「另一路」能到达的节点集合 B：从没选的端口连出的节点出发，同样求一遍。
3. 还可能执行的节点集合 C：此刻还有未消费待执行数据的节点，以及从它们出发能走到的节点。
4. 标跳过的是 B 里去掉 A、去掉 C、并且这次运行里一次都没执行过的节点。

从两路都能到达的节点（例如合并节点和它的下游）在 A 里，不标。两路节点自己在环里时，选中的一路会绕回它自己，A 就包含另一路的全部节点，所以环里不会误标：下一轮它可能走另一路。被标跳过的节点按 2.2 节不写 `steps` 记录（它没执行），只在 `changes` 里记一行，推送层照常合并推出。

这条规则对人工确认是 CTO 的决定；对条件判断用同一条规则（推断，两种节点都是「两路选一路」）。运行结束时不再补标：执行次数为 0、又没被这条规则标到的节点保持「空闲」（推断）。

### 2.2 每执行一次写一条记录

记录存在 SQLite。主键是 `(run_id, step)`：`step` 是全局步号，这次运行的第几步，从 1 开始只增不减。另存 `node_id` 和 `round`：`round` 是轮次，指这个节点在这次运行里第几次执行。同一个节点在环里跑第二轮时新写一条，不覆盖第一轮（PRD 5.1）。界面上回边的「第 3 轮」就是回边指向的那个节点当前的 `round`。

每条记录存：输入、输出、状态、开始和结束时间、上游配置哈希（第 7.3 节）、输入来源（真实上游 / 缓存 / 手动固定 / 手填，PRD 第 5 节）、副作用执行方式（真执行 / 模拟 / 缓存）、幂等键、错误。动作节点另外存：动作名、动作清单版本、`goal_id`、发出时间、下推给控制器的到期时间、最后一条反馈、最终状态、结果里实测的状态、取消记录、读回的机器人状态（第 2.5.3、11 节）。完整字段见第 11 节。

输入输出必须能序列化成 JSON。服务端用节点类型的 Pydantic 模型 `model_dump_json()` 序列化，失败就把这一步记为出错，错误写「输出不能序列化成 JSON」。

图像和传感器原始数据不写盘（PRD 第 2 节，推断：按 Torvalds 的理解，Alex 没反对）。相机节点的输出是一个图像引用 `{"image_ref": ..., "width": ..., "height": ..., "bytes": ..., "mime": ..., "captured_at": ...}`；记录里只存这个引用，也就是尺寸、大小、拍摄时间，加上记录本来就有的轮次和步号；图像本身放在进程内存里，按运行保留最近的若干张，超出就丢最老的（推断：张数等 Torvalds 按内存上限定）。服务重启后图像全部没了，记录还在，界面在「输出」标签里写「图像已不在内存里」（推断的文案）。下游节点读一张已经丢掉的图，这一步记出错，错误写「图像已不在内存里，请重新拍」（推断）。

每一步用两个事务落盘，顺序不能换：

1. 开始事务 T1：插入这一步的记录，状态 `running`，写好输入和幂等键；把这一步取走的待执行数据标成已消费（`pending.consumed_step = step`）。提交后才调用节点。动作节点提交 T1 以后才让 adapter 发目标。
2. 节点返回后（动作节点是拿到最终结果后），事务 T2：写输出、状态、结束时间；为每条出线插入新的待执行数据（`produced_step = step`）。提交后才通知推送层。

动作节点在 T1 和 T2 之间还有一次小的写：adapter 报回 `goal_id` 时立刻写进这一步的记录（推断）。这样服务在动作执行中崩溃，重启后知道去控制器问哪个目标（第 8 节）。反馈不逐条写盘，只在内存里保留最后一条，T2 时写进记录（推断：反馈频率由控制器定，可能很高）。

T1 先提交，节点调外部或发动作之前幂等键就已经在盘上了；服务在 T1 和 T2 之间崩溃时，重启后能看到一条状态还是 `running` 的记录，知道这一步做到一半（第 8 节）。T2 把输出和下游的待执行数据放在同一个事务里，重启后不会出现「输出写了、下游数据没写」或者反过来的情况，环不会丢一轮（PRD 5.1：落盘时还要存已经沿线发出、下游还没读的数据）。

`pending` 的行不删，只记 `produced_step` 和 `consumed_step`。任意第 k 步之前的队列都能从表里算出来：`produced_step < k`，并且 `consumed_step` 为空或 `>= k`。「从某个节点重跑」靠这条算出当时的队列（第 7.5 节）。

SQLite 设置：`PRAGMA journal_mode=WAL`（[WAL 文档](https://sqlite.org/wal.html)：读写可以同时进行，同一时刻只有一个写者）；`PRAGMA synchronous=FULL`。SQLite 文档写明，WAL 模式下 `NORMAL` 在应用崩溃时不丢已提交的事务，但断电或系统崩溃时可能回滚最近的事务；`FULL` 每次提交多同步一次 WAL 文件，断电后也不丢（[pragma synchronous](https://sqlite.org/pragma.html#pragma_synchronous)）。选 `FULL` 的理由：丢掉一条动作节点的记录，重启后就不知道机器人做过什么。一次运行的步数在千级（默认上限 1000），每步多一次同步的开销可以接受（推断）。所有写入走同一个写连接，放在一个专用线程里，执行协程用 `asyncio.to_thread` 或队列把写请求交给它（推断）。数据库文件放在控制器的本地盘上；写盘的 I/O 也会和控制程序抢，所以写线程同样用普通优先级（第 0.1 节，推断）。

### 2.3 节点接口

节点类型用一个 Python 类声明，输入、输出、参数各是一个 Pydantic 模型。动作节点的类由 adapter 交出的动作说明自动生成（第 2.5.1 节），手写的只有控制节点和测试用节点：

```python
from enum import Enum
from typing import ClassVar
from pydantic import BaseModel


class SideEffect(str, Enum):
    none = "none"  # 不读也不动：纯计算、控制节点
    read = "read"  # 只读传感器或外部：相机、关节角、夹爪读数、读笔记
    motion = "motion"  # 会让机器人动：走、转、坐、机械臂到某位姿、抓、放
    write = "write"  # 只给测试用节点（append_note）；机器人的动作不用这一类


class ReturnMode(str, Enum):
    blocking = "blocking"  # 做完才返回
    goal_progress = "goal_progress"  # 马上返回，之后收进度和结果


class NodeType:
    type_id: ClassVar[str]  # 动作节点是 "action:<动作名>"，例如 "action:walk_to"
    title: ClassVar[str]  # 界面上的中文名
    Inputs: ClassVar[type[BaseModel]]
    Outputs: ClassVar[type[BaseModel]]
    Params: ClassVar[type[BaseModel]]
    side_effect: ClassVar[SideEffect]
    side_effect_note: ClassVar[str] = (
        ""  # 悬停说明；motion 节点是「会让机器人动」（界面规格第 16 节）
    )
    timeout_s: ClassVar[float] = 60  # 控制节点和测试节点的推断默认值；动作节点取动作说明里的值
    action: ClassVar["ActionDescriptor | None"] = None  # 动作节点才有，第 2.5.1 节

    async def run(self, inputs: BaseModel, params: BaseModel, ctx: "RunContext") -> BaseModel: ...
    def mock(
        self, inputs: BaseModel, params: BaseModel
    ) -> BaseModel: ...  # 只有 write 类测试节点实现
```

`RunContext` 只给节点这些东西：`run_id`、`step`、`round`、`idempotency_key`，动作节点另外拿到 adapter 的调用句柄（只能调用自己这个动作，第 2.5.2 节）。不给图、不给别的节点、不给运行记录，节点没有办法绕过连线传数据。

服务端用 Pydantic 的 `model_json_schema()` 导出三个模型的 JSON Schema（[Pydantic JSON Schema 文档](https://docs.pydantic.dev/latest/concepts/json_schema/#generating-json-schema)，生成的 schema 符合 JSON Schema Draft 2020-12），通过 `GET /api/node-types` 给前端。前端用输入、输出 schema 画端口，用参数 schema 画右侧面板的参数表单；动作说明里写了单位和范围的，放进字段的 `json_schema_extra={"unit": "m", "minimum": ..., "maximum": ...}`，界面规格第 16 节据此在输入框里写「0.5 m」，没写范围的显示「控制器没给范围」，服务端不编默认范围。端口类型名放在 `json_schema_extra={"port_type": "text"}` 里，前端拖线时比较这个名字，不同就让目标端口变灰（界面规格第 6 节）；服务端保存和启动运行时再查一遍。`any` 类型的端口接受任何类型（推断）。图像端口的类型名是 `image`（推断）。

副作用类别由节点作者声明，动作节点由动作说明声明，执行器不自动判断（PRD 第 5 节）。动作说明里没写副作用的动作，一律按 `motion` 处理（PRD 第 5 节，Steve、Tedrake 2026-10-31）：

| 类别 | 整条运行里 | 单跑节点、单跑分组、从某节点重跑时 |
|---|---|---|
| `none` | 照常执行 | 照常执行 |
| `read` | 照常执行 | 默认用缓存：找同一节点、同一配置哈希、同一输入 sha256 的最近一条成功记录，直接用它的输出，记录里副作用执行方式写「缓存」。找不到就真读。传感器读数的输出都带采集时间 `captured_at`，用缓存时界面在入口端口写出采集时间（推断）。缓存的是图像而图像已经不在内存里（第 2.2 节），就真读一次 |
| `motion` | 经当前 adapter 真执行 | 没有模拟值（PRD 第 10 节：「不能模拟返回」）。同样经当前 adapter 真执行；adapter 报 `target = real` 时，请求要带确认文字（第 7.1 节）。假 adapter 本身就是仿真，不用确认（推断） |
| `write`（只给测试节点） | 真执行，带幂等键 | 默认返回 `mock()` 的模拟值，记录里写「模拟」，界面状态词后加「· 模拟」。用户在右侧面板点「真执行」并确认后才真执行，带幂等键 |

幂等键 = `run_id + ":" + step`。这是 Temporal 对 Activity 的建议做法：用工作流的 Run ID 加 Activity ID 作幂等键，重试时不变、不同执行之间不重复；去重由被调用的外部服务做（[Temporal：Activity 幂等](https://docs.temporal.io/activity-definition#idempotency)）。LangGraph 文档也说节点在恢复后会从头重跑，节点前面做过的副作用会再做一次，所以要用幂等键、upsert 或先读后写（[LangGraph：Re-execution and idempotency](https://docs.langchain.com/oss/python/langgraph/graph-api#re-execution-and-idempotency)）。

幂等键对 `write` 有用，对 `motion` 没用。`motion` 节点也拿到这个键，adapter 把它记进日志、能放就放进目标里，方便事后对照是哪一步发的；但执行器不靠它去重（PRD 第 10 节：「不能靠幂等键去重」）。`motion` 的「再做一次」永远是新的一步、新的目标，由人确认后才发（第 2.6 节）。

### 2.4 控制节点和测试用节点

PRD 第 10 节：动作节点由 adapter 生成，`node-list-v0.md` 作废，原来的 9 个节点只留着测执行器；手写的只剩控制节点，和 Tedrake 一起定哪些留下。v0.2 的划分（推断，等 Tedrake 定）：

| 节点 | 分组 | 副作用 | 执行器要注意的 |
|---|---|---|---|
| 开始 | 控制 | none | 没有输入。运行开始时把请求里的初始数据作为它的输出，记为第 1 步 |
| 输出 | 控制 | none | 没有输出端口。执行后把输入记成这次运行的最终结果 |
| 常量 | 控制 | none | 没有输入，运行开始时就绪 |
| 条件判断 | 控制 | none | 参数是一条简单比较（字段、比较符、值）。只往「是」或「否」一路发数据。是环的出口 |
| 合并 | 控制 | none | 所有输入端口都有数据才就绪 |
| 人工确认 | 控制 | none | 执行时把运行停住，暂停原因写 `approval`，等 `POST /api/runs/{run_id}/approve`。通过就往「通过」一路发，驳回往「驳回」一路发，同时按 2.1 节立刻标跳过。等待期间 `continue`、`step` 返回 409 `awaiting_approval`（第 5 节）。机器人在这段时间停在上一个动作结束时的状态 |
| 文本模板 | 仅测试 | none | 所有文本输入都到齐才就绪 |
| 模型调用 | 仅测试 | read | 单跑和重放时用缓存（PRD 8 第 11 条）。超时默认值要比其他节点长（推断） |
| 工具调用 | 仅测试 | 按所调工具 | 见下文 |

界面规格第 16 节的控制节点分组里还有「循环」。本规格里环是连线连出来的，靠条件判断退出，没有单独的循环节点；要不要加，见文末对不上的地方。

「仅测试」节点在节点面板里放进单独的「测试」组，悬停写「只用来测执行器」（推断）。

工具调用的副作用（推断）：每个工具单独声明副作用类别；工具调用节点的参数里有一个「允许的工具」列表；只要列表里有一个 `write` 工具，这个节点在画布上就带 ↗ 标记；执行时按输入里实际的工具名取类别，再按 2.3 节的表决定真执行、模拟还是缓存；工具名不在允许列表里就记出错。工具调用节点不能调用机器人的动作：动作只能做成动作节点，放在画布上，断点和停止才管得到（推断）。

三个测试用工具（推断，名字和行为都可改）。它们都改名为「仅测试」工具，节点面板和记录里都这样标：

- `read_note`（仅测试，read）：从笔记库读一条笔记的全部行。`read_note` 只开只读连接。
- `append_note`（仅测试，write）：往一条笔记追加一行。笔记内容和幂等键台账放在同一个它自己的 SQLite 文件里（推断文件名：数据目录下 `demo_notes.db`），不放在运行记录库 `runs.db` 里。追加一行和登记幂等键必须在同一个 SQLite 事务里提交（CTO 定）：

  ```sql
  BEGIN IMMEDIATE;
  SELECT result_json FROM idem_keys WHERE idem_key = ?;      -- 有就 ROLLBACK，直接返回这份结果
  INSERT INTO note_lines (note_id, line_no, line, idem_key) VALUES (?, ?, ?, ?);
  INSERT INTO idem_keys (idem_key, result_json, at) VALUES (?, ?, ?);
  COMMIT;
  ```

  两条 `INSERT` 要么都在、要么都不在，所以不会出现「行写进去了、键没登记」：进程在 `COMMIT` 之前死掉，SQLite 回滚这个事务，重试时台账里没有这个键，就写一次；在 `COMMIT` 之后死掉，重试时查到键，直接返回上次结果。`idem_keys.idem_key` 设成主键，`note_lines.idem_key` 加唯一约束，两头都挡住重复（推断）。验收用 `note_lines` 数「外部真的写了几次」（第 13 节 A1、A12、A18）。PRD 第 2 节说「不写的是写本地文件的演示工具」，和这一条冲突，见文末对不上的地方；在定下来之前照 PRD 第 9 节「A18、A20 保持原样」。
- 换成真的外部写服务时做不到和它同一个事务，只能靠对方按幂等键去重（2.3 节）。`append_note` 能把这件事做严，用来证明执行器这一侧的幂等键传对了。
- `clock`（仅测试，none）：返回固定格式的当前时间，用来测 `none` 节点重放照常执行。

### 2.5 执行器里的 adapter

adapter 是对接机器人控制器的那层代码，针对每种控制器单独写；控制器是哪家、走 ROS 2 还是厂商 SDK、接真机还是仿真，差别都藏在 adapter 里（PRD 第 2 节，Alex 2026-10-31）。执行器只认下面这几个操作。

adapter 分两半（推断，进程边界等 Tedrake 确认）：

- 客户端：一个 Python 对象，在后端进程里，执行器通过它调用。
- 守护进程：在控制器上单独跑的一个进程，真正向控制器发目标、收反馈和结果、发取消。它收后端的心跳（第 2.5.4 节）。

分成两半的理由：如果发目标的那一方就在后端进程里，后端一死，没有人去取消，而 ROS 2 动作服务端发现不了客户端死了（[rclcpp#2241](https://github.com/ros2/rclcpp/issues/2241) 的复现：「On the server side the goal is still reported as active and executing if the client died.」维护者回复：「action server does not have connectivity map」，见调研附录 `embodied-exec-v0.md` 第 5.5 节）。守护进程自己也会死，所以最后一层靠控制器自己的到期时间（第 2.5.4 节）。

假 adapter（v1 用的）实现同样的接口，带几个编出来的动作，名字标明是假的，`target = sim`；它就是仿真（PRD 第 7 节）。假动作由 Torvalds 定。

```python
class Adapter(Protocol):
    name: str                            # 例如 "fake-adapter"，名字标明是假的
    version: str                         # 动作清单版本，和 list_actions() 返回的一致（Torvalds 已实现）
    target: Literal["sim", "real"]       # adapter 整体报一个，界面只看它显示红框「真机」，不从名字猜（Tedrake 定的字段名，Torvalds 已在代码里改）
    heartbeat: HeartbeatSpec             # 第 2.5.4 节

    async def list_actions(self) -> ActionList                      # 2.5.1
    async def send_goal(self, action: str, goal: dict, tag: str, expires_at: datetime) -> GoalHandle  # 2.5.2
    def feedback(self, h: GoalHandle) -> AsyncIterator[dict]          # 2.5.2，blocking 动作没有反馈
    async def result(self, h: GoalHandle) -> ActionResult             # 2.5.2
    async def cancel(self, h: GoalHandle) -> CancelResponse           # 2.5.3
    async def read_state(self, action: str | None = None) -> RobotState  # 读回当前状态
    async def active_goals(self) -> list[GoalInfo]                    # 第 8 节重启时用
```

#### 2.5.1 `list_actions()`：动作清单和动作说明

`list_actions()` 返回 `{version, actions: [...]}`。`version` 是清单版本号，控制器能给就用控制器的，给不了就用整份清单的规范化 JSON 的 sha256（推断）。控制器能列出自己的动作就从控制器读；列不出，就把动作表手写在 adapter 里，这是 adapter 自己的事（PRD 第 5 节）。

每个动作的说明分两层（Tedrake，PRD 第 5 节）：

- 自动读出的一层：名字，以及目标、反馈、结果三组字段的名字和类型。走 ROS 2 时从 `.action` 接口定义读：第一个 `---` 之前是请求（目标）字段，之后是结果字段，第二个 `---` 之后是反馈字段（[ROS 2 文档：Interfaces · Actions](https://docs.ros.org/en/jazzy/Concepts/Basic/About-Interfaces.html#actions)，2026-10-31 对照仓库源文件核对）。ROS 2 类型到 JSON Schema 的对应：`bool`→`boolean`，各种整数→`integer`，`float32`/`float64`→`number`，`string`→`string`，数组→`array`，嵌套消息→`object`（推断）。
- adapter 手写的一层：每个动作写一次的小表，下表里「手写」那些字段。

合并后的字段表（Thompson、Tedrake）。「必填」一列写「是」的缺了，这个动作不生成节点，节点面板在「控制器的动作」组下面列一行错误：「<动作名>：缺 <字段>」（推断的文案）；`GET /api/node-types` 的 `rejected_actions` 给出同样的信息。

| 字段 | 哪一层 | 必填 | 内容 | 执行器拿它做什么 |
|---|---|---|---|---|
| `id` | 自动 | 是 | 动作名，例如 `walk_to` | 节点类型 id `action:<id>` |
| `title` | 手写 | 否 | 中文名，例如「走到」；没写就显示 `id` | 节点标题 |
| `goal` | 自动 | 是 | 目标字段：名字、类型 | 节点的参数和输入端口 |
| `goal[].unit` | 手写 | 否 | 单位。约定用 SI 单位：米、弧度、秒（[REP 103](https://www.ros.org/reps/rep-0103.html)，只取它的单位约定） | 输入框里显示单位 |
| `goal[].range` / `goal[].enum` | 手写 | 否 | 取值范围或可选值 | 参数校验；没写就不校验范围，界面写「控制器没给范围」 |
| `goal[].default`、`goal[].required` | 手写 | 否 | 默认值、是否必填 | 参数表单 |
| `feedback` | 自动 | 否 | 反馈字段 | 节点上「运行中 · 40%」或最近一条反馈的原文（PRD 第 5 节） |
| `result` | 自动 | 是 | 结果字段，类型里可以有「图像」 | 节点的输出端口 |
| `result.measured_state` | 自动或手写 | `motion` 必填 | 结果里带的结束时实测状态，例如到达的位姿、夹爪开合 | 写进记录，右侧面板显示 |
| `side_effect` | 手写 | 否 | `none` / `read` / `motion`；没写一律当 `motion` | 第 2.3 节的表 |
| `return_mode` | 手写 | 是 | `blocking`：做完才返回；`goal_progress`：马上返回，之后收进度和结果 | 第 2.5.2、2.6 节 |
| `timeout_s` | 手写 | 是 | 最长执行时间 | 下推给控制器当到期时间，执行器自己的超时也按它算（第 2.5.4 节） |
| `typical_s` | 手写 | 否 | 典型耗时 | 界面写「通常 N s」（推断）；不参与超时 |
| `expiry` | 手写 | `motion` 必填 | `controller`：控制器接受一个到期参数（Spot 的 `end_time` 这一类），adapter 把 `timeout_s` 换算成它下推；`adapter_only`：控制器没有到期参数，说明里必须写「到期只靠 adapter」 | `adapter_only` 的动作，参数表单底部写「控制器不接受到期时间，后端卡住时这一步停不下来」（界面规格 16.y；第 2.5.4 节） |
| `expiry_param` | 手写 | `expiry = controller` 时必填 | 控制器那边的参数名和时间基准，例如「`end_time`，机器人时钟」 | adapter 换算用 |
| `cancel.cancellable` | 手写 | 是 | 默认能不能取消 | 停止时调不调取消（第 2.5.3 节）。ROS 2 下注意 rclpy 默认拒绝取消（上文第 3 条） |
| `cancel.method` | 手写 | `cancellable = true` 时必填 | 取消具体做什么，例如「发 action 取消」「调 SDK 的 StopMove」 | 写进取消记录 |
| `cancel.after` | 手写 | `motion` 必填 | 取消后机器人停在哪，由控制器决定，按动作写，例如「原地站立，臂保持当前位姿，夹爪不松」 | 界面在停止前后显示；读回时对照 |
| `preconditions` | 手写 | 否 | 执行前要满足的状态，例如「站立」「未在阻尼」，写成对 `read_state` 字段的简单比较（推断，比较规则同断点条件，第 3 节） | 发目标前读一次状态检查，不满足不发，这一步记出错 `precondition_failed` |
| `read_state` | 手写 | `motion` 必填 | 怎么读回当前状态、读哪些字段；机械臂夹着东西的动作要能读出夹爪是否夹着东西 | 结果未知、停止、重启后读回（第 2.6、8 节） |

adapter 整体（不是每个动作）还要给：`name`、`target`、清单 `version`、`heartbeat`（Torvalds 已实现前三个；`heartbeat` 推断）（第 2.5.4 节）。

相机画面是一个由 adapter 提供的动作节点（Alex 2026-10-31，PRD 第 5 节）：`side_effect = read`，结果里有一个图像字段，`return_mode` 一般是 `blocking`（推断）。记录只存图像的元数据，不存图像本身（第 2.2 节，推断）；界面在右侧面板「输出」标签里显示整张图，图下写拍摄时间、第几轮、第几步（界面规格第 16 节）。

#### 2.5.2 发目标、收反馈、收结果

执行器对动作只用四个操作：发目标、收反馈、收结果、取消。走 ROS 2 时直接对应 ROS 2 action；走厂商 SDK 时，adapter 把 SDK 包成同样的四个操作。

ROS 2 文档对 action 的说法：「an action refers to a long-running remote procedure call with feedback and the ability to cancel or preempt the goal」，动作服务端「should react to cancellation/preemption requests」，并且同一个动作名只应有一个动作服务端（[ROS 2 文档：Actions](https://docs.ros.org/en/jazzy/Concepts/Basic/About-Actions.html)；这个页面有防机器人检查，2026-10-31 对照 `ros2/ros2_documentation` 仓库 jazzy 分支的源文件核对）。

| 执行器的操作 | ROS 2 action | 厂商 SDK（adapter 包成这样） |
|---|---|---|
| 发目标 `send_goal` | 发目标请求，服务端回接受或拒绝 | 调用开始动作的函数 |
| 收反馈 `feedback` | 订阅反馈 | `goal_progress` 时轮询进度；`blocking` 时没有 |
| 收结果 `result` | 请求结果，拿到最终状态：`SUCCEEDED=4`、`CANCELED=5`、`ABORTED=6`（`action_msgs/msg/GoalStatus`） | `blocking` 是函数返回值；`goal_progress` 是查到的最终状态 |
| 取消 `cancel` | 发取消请求，回应码：`ERROR_NONE=0`（接受）、`ERROR_REJECTED=1`、`ERROR_UNKNOWN_GOAL_ID=2`、`ERROR_GOAL_TERMINATED=3`（`action_msgs/srv/CancelGoal`） | 调用 `cancel.method` 写的停止函数 |

`GoalStatus`、`CancelGoal` 的取值 2026-10-31 在 [`ros2/rcl_interfaces` jazzy 分支 action_msgs](https://github.com/ros2/rcl_interfaces/tree/jazzy/action_msgs) 核对。ROS 2 设计文档写明，取消的回应只说明目标进入了取消中，「Whether or not a goal transitions to the CANCELED state is indicated by the status topic and the result service.」（[ROS 2 Actions 设计文档](https://design.ros2.org/articles/actions.html)，见调研附录第 5.4 节）。所以「取消被接受」不等于「已停下」，要等结果。

两种返回方式（Alex 2026-10-31：动作怎么返回因动作而异；PRD 第 5 节）：

| | `blocking`（做完才返回） | `goal_progress`（马上返回，之后收进度和结果） |
|---|---|---|
| 执行器怎么调 | 在一个任务里 `await` adapter 的调用，返回值就是结果 | `send_goal` 拿到句柄，`goal_id` 立刻写进记录；另开任务收反馈，主任务等 `result` |
| 节点什么时候算完成 | 调用返回 | 拿到最终结果。`send_goal` 返回不算完成 |
| 节点上显示 | 「运行中 · 12 s」，按秒计时 | 「运行中 · 40%」或最近一条反馈的原文 |
| 有没有 `goal_id` | 可能没有（看 SDK） | 有 |

两种方式下，节点都显示「运行」直到拿到最终结果，断点只在节点之间生效（PRD 第 5 节）。

#### 2.5.3 取消：停止时调用当前动作的取消

用户点停止时，正在执行的是动作节点，执行器调用它的取消（第 5 节）。取消要做的事，按顺序（调研附录第 8 节，推断，接口名由 Torvalds 定）：

1. 看 `cancel.cancellable`。为 `false` 就不调，记 `cancel_called = false`、原因「这个动作不能取消」，等它自己结束。
2. 按 `cancel.method` 发取消，记调用时间。
3. 等回应，有上限时间（推断：不超过这个动作的 `timeout_s`）。记回应：接受、拒绝、不认识这个目标、目标已结束、没回应、出错（带原文）。
4. 回应是接受时，接着等最终结果（上一节：接受不等于已停下）。
5. 读一次机器人状态（`read_state`），连同读取时间记下；机械臂相关的动作单独记夹爪是否夹着东西。
6. 不发任何新的运动：不「回安全位置」，不坐下（PRD 第 7 节：画布的停止建议做成停在原地、电机保持供电；界面规格 16.x 同。Alex 可改，见第 16 节）。
7. 取消本身出错或超时，不吞掉，照样记下。

要记的字段（写进这一步的记录，`cancel_json`）：

| 字段 | 内容 |
|---|---|
| `cancel_called` | 是否调用了取消 |
| `cancel_called_at` | 调用时间 |
| `cancel_trigger` | `stop` / `timeout` / `operator_lost` |
| `cancel_method` | 实际用的方法 |
| `cancel_response` | `accepted` / `rejected` / `unknown_goal` / `terminated` / `no_response` / `error`，加原文 |
| `goal_id`、`goal_final_status` | 目标 id 和最后看到的状态（ROS 2 下是 `GoalStatus` 的值，看不到写 `unknown`） |
| `state_after` | 读回的状态和读取时间；读不到写原因 |
| `gripper_holding` | 读回的夹爪是否夹着东西；这个动作不涉及夹爪写 `n/a`，读不到写 `unknown` |

取消的结果怎么变成节点状态：

| 情况 | 节点 | 运行 |
|---|---|---|
| 取消被接受，最终状态是已取消，读回状态正常 | `stopped` 已停止 | `stopped` |
| 取消被接受，但动作在取消前已经做完（最终状态是成功） | `done` 完成，记录照常 | `stopped` |
| 取消被拒，或这个动作不能取消 | 仍是 `running`，状态词后加「· 取消被拒」（界面规格第 16 节） | 仍是 `running`，带 `stop_requested`；不再派发任何节点；等动作自己结束后按真实结果记这一步，运行以 `stopped` 结束 |
| 没回应、出错、读不到状态、等结果超时 | `interrupted_unknown` 中断 · 结果未知 | `stopped`（推断：用户要的是停，不留在暂停里） |

取消被拒时，顶栏写「停止被拒」，旁边一句「机器人仍在执行，需要急停请用硬件」（界面规格第 16 节）。用户可以再按一次停止，就是再调一次取消，每次都记一条（推断：`cancel_json` 存成列表）。

#### 2.5.4 到期时间和心跳：后端死了以后谁让机器人停

三层，各管一段（调研附录第 6.2 节，推断）：

1. 控制器自己的到期时间。`expiry = controller` 的动作，adapter 发目标时把 `timeout_s` 换算成控制器的到期参数一起发。Spot 的做法：每条运动指令都必须带 `end_time`，原文「This is a required field and used to prevent runaway commands.」，到期后指令反馈是 `STATUS_COMMAND_TIMED_OUT`（[Spot 协议参考](https://dev.bostondynamics.com/protos/bosdyn/api/proto_reference)，见调研附录第 5.6 节）。这一层不依赖后端和 adapter 守护进程活着。
2. adapter 的心跳。后端每隔 `heartbeat.interval_s` 向 adapter 守护进程发一次心跳；守护进程超过 `heartbeat.timeout_s` 没收到，就对它发出的、还在执行的每个目标调用取消，并把这件事记进自己的日志（Tedrake 2026-10-31）。两个值由 adapter 声明，本规格不写数字（没有出处）。rclcpp#2241 里也有用户说他们的做法是「using a second heartbeat topic in both ways」（见调研附录第 5.5 节）。心跳要从执行协程所在的事件循环发出，不要放在单独线程里（推断：单独线程在事件循环卡住时照样发心跳，守护进程就发现不了后端卡死）。
3. 硬件急停。不在这个后端里，见第 5 节。

`expiry = adapter_only` 的动作只有第 2 层。adapter 守护进程也卡死时，这个动作没有人停，所以说明里必须写「到期只靠 adapter」，界面在参数表单底部写「控制器不接受到期时间，后端卡住时这一步停不下来」，画布节点上不加标记（界面规格 16.y）。

执行器自己的超时（第二道）：每次调用 adapter 都把到期时间一起传过去（第一道，adapter 再转成控制器的到期参数）；Python 这边另套 `asyncio.timeout(60)`（Torvalds 已实现的默认值，没有出处，可配置）。动作的 `timeout_s` 超过 60 s 时，这一道要相应放大，否则会在控制器到期前误判（推断）。所以正常情况下先到期的是控制器，执行器收到的是一个已知结果（第 2.6 节的「控制器到期」一行）；执行器的超时只在控制器没按时报结果时才触发。

### 2.6 `motion` 节点在暂停、断点、停止、超时、崩溃时怎么做

`motion` 和 `write` 的区别（调研附录第 3 节）：不能模拟（假的「已走到」会让下游按错误的位置去抓）；不能靠幂等键去重；结果未知时只能读机器人现在的状态，没有台账可查。

| 情况 | `blocking` | `goal_progress` | 节点状态 | 运行 |
|---|---|---|---|---|
| 用户点暂停 | 不打断，等调用返回 | 不取消，等最终结果 | 结果到了按结果记 | 结果写盘后停在下一个节点执行前，原因 `user`（推断：暂停不调取消；要停下动作用停止） |
| 断点 | 只在节点之间。执行前断点：目标还没发。执行后断点：动作已结束 | 同左 | — | 停住期间机器人停在上一个动作结束时的状态，停住时执行器读一次状态放进 `pause.robot_state`（推断） |
| 单步 | 只执行一个动作，结果到了再停 | 同左 | — | `step_done` |
| 停止 | 调取消（第 2.5.3 节）；SDK 没有单独的停止函数时只能等返回 | 对句柄调取消，再等结果 | 见第 2.5.3 节的表 | 见第 2.5.3 节的表 |
| 控制器到期（第 2.5.4 节第 1 层先触发） | 调用返回到期 | 结果是已中止 | `error`，错误类型 `expired`，带结果里的实测状态（推断：控制器已经停下了，结果是已知的） | `error`，和别的出错一样 |
| 执行器超时（控制器没按时报结果） | 先调取消，再读状态 | 同左 | `interrupted_unknown`，错误类型 `timeout` | `paused`，原因 `motion_timeout`（第 9 节） |
| 前提条件不满足 | 不发目标 | 同左 | `error`，错误类型 `precondition_failed` | `error` |
| 控制器拒绝目标 | 不算执行过 | 同左 | `error`，错误类型 `goal_rejected` | `error` |
| adapter 断开（后端定时探测 adapter，3 s 没回应（Torvalds 已实现的默认值，没有出处，可配置）） | 不知道动作停没停 | 同左 | 正在执行的 `motion` 节点记 `interrupted_unknown`，错误类型 `adapter_lost`；没有动作在执行时不改节点 | `paused`，原因 `adapter_lost`，顶栏「已暂停 · 机器人控制器断开」；adapter 重连前继续、单步置灰 |
| 后端进程崩溃 | 心跳停，adapter 取消；控制器到期兜底 | 同左 | 重启后 `interrupted_unknown` | 重启后 `paused`，原因 `restart`（第 8 节） |

结果未知（`interrupted_unknown`）以后怎么往下走，三种情况（`motion_timeout`、`adapter_lost`、`restart`）都一样：

1. 运行停在这个节点执行前。执行器不自动重发。
2. adapter 现读一次机器人状态（`POST /api/runs/{run_id}/readback`，或执行器暂停时自动读一次），结果连同读取时间和 `readback_id` 放进 `pause.readback`。重启后还要先问控制器这个目标还在不在执行（第 8 节）。
3. 用户看过机器人、看过读回的状态，选一种，请求里带 `readback_id`：
   - 重做这个动作（`resolution = retry`）：新的一步、新的目标，记录的 `retry_of` 指向原来那一步。参数可以先改。
   - 当作已做完（`resolution = mark_done`）：用户填这一步的输出（可以从读回的状态预填），输入来源记「手填」，记录里写「人工确认」和确认时用的读回。
   - 停止。
4. 继续和单步不带 `readback_id`，或者 `readback_id` 不是最新一次读回的，返回 409 `readback_required`（推断的错误码）。读回的时间早于结果未知发生的时间，同样返回 409。

`write`（只有测试节点）结果未知时照 v0.1：带原幂等键重试，不要求读回。

---

## 3. 碰到断点暂停

断点挂在流程上，不进流程版本：`breakpoints` 表按 `(flow_id, node_id, when)` 存，`when` 是 `before`（执行前）或 `after`（执行后）。运行中设或删断点立刻生效，执行器每次检查都读当前的断点表。

暂停用 `asyncio.Event` 实现。`asyncio.Event` 内部有一个标志，`wait()` 一直等到别处调用 `set()` 把标志置真，`clear()` 把标志清掉（[Python 文档](https://docs.python.org/3/library/asyncio-sync.html#asyncio.Event)）。每次运行的控制对象里有一个 `resume` 事件。执行协程在派发每个节点前后各过一次检查点：要停时 `resume.clear()`，写暂停状态，然后 `await resume.wait()`；继续、单步、停止这几条指令处理完后调 `resume.set()`。文档写明 asyncio 的同步原语不是线程安全的，所以 HTTP 指令必须在同一个事件循环里调用 `set()`；如果框架把处理函数放到别的线程，要用 `loop.call_soon_threadsafe(resume.set)`。

执行前断点停在 T1 之前：这一步的记录还没写，待执行数据也还没标成已消费，动作节点的目标还没发。所以停在第 3 个节点前时，它和第 4 个节点都没有新记录（PRD 8 第 2 条）；在环里第 2 轮停下时，这个节点只有第 1 轮的记录（PRD 8 第 16.1 条）。

执行后断点停在 T2 之后：输出和下游的待执行数据都已落盘，但下游还没派发。动作节点到这里动作已经结束。

断点不会停在动作中途，两种返回方式都一样（第 2.6 节）。

断点怎么命中（PRD 5.1）：

| 模式 | 什么时候停 |
|---|---|
| `every`（默认） | 环里每一轮都停 |
| `round` | 只在这个节点的第 N 轮停。执行前断点看即将执行的那一轮，执行后断点看刚执行完的那一轮 |
| `condition` | 一条简单比较成立时停：`{"field": "a.b", "op": "eq", "value": 3}`。`field` 只能是这个节点输入模型里已有的字段（设断点时按输入 schema 校验，不存在就返回 422）；`op` 只有 `eq ne lt le gt ge contains empty`；`value` 是 JSON 字面量。不能写 Python，也不能比较两个字段。比较时类型对不上算不成立 |

停下时服务端生成一个新的 `pause_id`，连同暂停原因、节点、`before`/`after`、步号、轮次写进 `runs.pause_json`，然后把推送层里还没发的状态立刻推出去，再推一条 `paused` 事件（第 10.2 节）。暂停原因：

| `reason` | 什么时候 | 继续要什么 |
|---|---|---|
| `breakpoint` | 断点命中 | `pause_id` |
| `user` | 用户点了暂停 | `pause_id` |
| `step_done` | 单步走完 | `pause_id` |
| `approval` | 人工确认 | 只能 `approve`（第 5 节） |
| `restart` | 服务重启后恢复（第 8 节） | `pause_id`；停着的是结果未知的动作节点时，还要 `readback_id` 和 `resolution`（第 2.6 节） |
| `storage_error` | 记录写不进去（第 12 节） | `pause_id` |
| `write_timeout` | 测试用 `write` 节点超时（第 9 节；推断的名字） | `pause_id` |
| `motion_timeout` | 动作节点执行器超时（第 2.6、9 节；推断的名字） | `pause_id`、`readback_id`、`resolution` |
| `adapter_lost` | 后端探测 adapter，3 s 没回应（Torvalds 已实现的默认值，没有出处，可配置）；顶栏「已暂停 · 机器人控制器断开」（第 2.6 节） | adapter 重连，再要 `pause_id`、`readback_id`、`resolution` |
| `operator_lost` | 没有任何页面连着超过 10 s（Torvalds 已实现的默认值，没有出处，可配置）；顶栏「已暂停 · 页面断开」（第 5.1 节） | `pause_id` |

用户点「暂停」（`POST /api/runs/{run_id}/pause`）：正在执行的节点不打断，执行完、T2 落盘后停在下一个节点执行前。动作节点也一样，暂停不调取消（第 2.6 节）。

---

## 4. 看输入输出、改值

停住后右侧面板读 `GET /api/runs/{run_id}/steps?node_id=...`，按轮次一轮一条（界面规格第 9 节）。停在执行前断点时，这个节点还没有本轮记录，面板显示的输入来自 `GET /api/runs/{run_id}` 返回的 `pause.inputs`：执行器按 2.1 节的规则从待执行数据里预取出来、但还没标成已消费的那份。

改值不单独发请求，跟着「继续」或「单步」一起发（`edits` 字段）。好处是两个标签页各改各的不会互相覆盖，谁的继续先到就用谁的（第 12 节）。

- 停在执行前：`edits.inputs` 替换这一步的输入。服务端用输入模型校验，失败返回 422，运行保持暂停。记录里输入来源写「手填」，原值放在 `edited_json.original`。动作节点的目标参数也照样能改，改完照动作说明的范围校验。
- 停在执行后：`edits.outputs` 替换这一步的输出。服务端在一个事务里改这一步的输出和它刚发出、还没被消费的待执行数据，原值放在 `edited_json.original`。下游记录里收到的就是改后的值（PRD 8 第 3 条）。
- 动作节点（`motion`）的输出不能在执行后断点上改，返回 422 `motion_output_readonly`（推断的错误码）。把「抓取成功：否」改成「是」，下游会按世界里不存在的状态去做（调研附录第 5.2 节）。动作结果未知时用「当作已做完」填输出（第 2.6 节），那一条要先读回状态。

---

## 5. 单步、继续、停止

三条指令在运行暂停时才有效（停止除外，见下）。继续和单步必须带当前的 `pause_id`，和服务端的不一致就返回 409 `pause_mismatch`。

运行停在人工确认上（`status = paused`，`reason = approval`）时，继续和单步都返回 409，错误码 `awaiting_approval`，运行保持暂停，`pause_id` 不变；只有 `POST /api/runs/{run_id}/approve` 能让它往下走，停止照常可用（CTO 定）。服务端先查 `reason` 再比 `pause_id`：两样都不对时返回 `awaiting_approval`（推断）。

停在结果未知的动作节点上（`reason` 是 `restart`、`motion_timeout`、`adapter_lost`，并且停着的节点是 `motion`）时，继续和单步要带 `readback_id` 和 `resolution`，否则返回 409 `readback_required`（第 2.6 节）。

- 继续：清掉单步标记，`resume.set()`，一直跑到下一个断点、人工确认或运行结束。
- 单步：再执行一个节点，然后停在下一个要派发的节点执行前，暂停原因 `step_done`。如果下一个节点是回边指向的环开头，这一步就进入下一轮，界面显示轮数加一（PRD 5.1）。单步结束时推送层立刻推出未发的状态，不等 100 ms 窗口（PRD 8 第 19.2 条）。
- 停止：运行中、暂停中都能停。暂停中直接把运行标成「已停止」，不向机器人发任何东西。运行中有节点正在执行：
  - `none`、`read` 节点：取消它的 asyncio 任务，这一步记「已停止」。
  - `motion` 节点：调用这个动作的取消，按第 2.5.3 节的步骤做、按那里的表记状态。只取消 asyncio 任务没有用：取消的只是等结果的那个协程，机器人照样执行（调研附录第 5.4 节）。
  - 测试用 `write` 节点：被取消时不知道外部写没写，记「中断 · 结果未知」，保留幂等键。
  停止后待执行数据照样留在盘上，以后可以从某个节点重跑。

画布上的停止对应 IEC 60204-1 的第 2 类停止：受控停下，电机保持供电。Schneider Electric 对三类停止的摘要：「Category 0: stopping by immediate cutting off of power to the actuators (example: uncontrolled stop). Category 1: controlled stop by maintaining power to the actuators until the machine has stopped, then cutting off the power when stopping has been achieved. Category 2: controlled stop by maintaining power to the actuators.」（[Schneider Electric FAQ](https://www.se.com/us/en/faqs/FA122781/)，见调研附录第 5.4 节；标准原文没有核对）。PRD 第 7 节把画布的停止定为这一类的建议（停在原地、电机保持供电，非认证结论），按钮悬停文字照界面规格 16.x：「停在原地，电机不断电。急停请用硬件按钮。」动作停下后机器人处在什么状态由控制器决定，按动作写在 `cancel.after` 里，画布只显示 adapter 报上来的结果（PRD 第 5 节，Tedrake）。

急停不是画布的功能。ISO 13850 要求急停由「a single human action」触发、「available and operational at all times」，复位「shall not initiate machine start up」（[ISO 13850:2015 公开预览](https://cdn.standards.iteh.ai/samples/59970/06ad76004ab746ff90b82cf44a8b0752/ISO-13850-2015.pdf) 第 4.1.1 条，见调研附录第 6.1 节）。一个要经过浏览器、网络、Python 进程、adapter 的按钮做不到「任何时候都可用」，所以急停走机器人自己的硬件通路，不经过网页、HTTP 和这个后端（PRD 第 7 节）。画布上不放急停按钮（推断）。急停复位后，画布上的运行保持原状，不自动继续：急停期间正在执行的动作，adapter 报回来的多半是已中止或连接断开，按第 2.6 节的表处理。

### 5.1 页面断开

PRD 第 3 节第 14 步：关掉或刷新页面，运行在服务端继续或保持暂停。v0.2 照 Torvalds 已实现的规则改（调研附录第 6.4 节提的就是这件事）：

- 这次运行没有任何页面连着（SSE 订阅者为 0）超过 10 s（Torvalds 已实现的默认值，没有出处，可配置），执行器按用户暂停处理：正在执行的节点不取消，做完后不派发下一个节点，暂停原因 `operator_lost`，顶栏「已暂停 · 页面断开」。
- 10 s 比 SSE 重连间隔 2 s（第 10.2 节）长，刷新一下页面不会触发。
- 这条对所有运行都生效，不只是带动作节点的（Torvalds 的实现）。
- 页面断开不是急停的替代。断开到暂停之间，安全只能靠控制器到期时间、adapter 心跳和硬件急停（第 2.5.4 节）。
- 断网时机器人怎么办，PRD 第 7 节写「按每个动作在 adapter 里写的来」；本条只管执行器不再派发下一个动作。

---

## 6. 版本锁定和「从新版本重跑」

运行中改画布不影响这次运行（第 1 节）。界面上的「从新版本重跑」发 `POST /api/runs`，带最新的 `flow_version`，从头开一次新运行（推断：PRD 没写从头还是从当前节点，这里取从头，因为新旧版本的节点和连线可能对不上）。旧运行如果还停着，保持原状，用户可以去停掉它。

锁定版本包括动作清单。保存流程时，`flow_versions` 记下当时的清单 `version`，以及流程里每个动作节点所用动作的说明哈希（整份动作说明的规范化 JSON 的 sha256，推断）。每次开跑（整条、单跑、分组、从某节点重跑）都按第 1 节第 2 步比一遍，对不上就在第一个节点执行前拒绝，不会跑到一半才失败（PRD 第 5 节，Thompson 推断）。

控制器更新后少了某个动作：用到它的节点照样留在画布上，显示「动作已不存在」（界面规格第 16 节），不自动删（PRD 第 5 节）。`GET /api/flows/{flow_id}/action-check?flow_version=` 返回每个动作节点的检查结果 `ok` / `action_missing` / `action_changed`，界面打开流程时就能标出来，不用等点运行（推断的端点）。

动作说明哈希也进节点的配置哈希（第 7.3 节）：动作定义变了，下游的输入快照会标成可能过期。

---

## 7. 单跑一个节点或一组

单跑、分组跑、从某节点重跑都开一次新的运行，`kind` 分别是 `node`、`group`、`rerun`，有自己的 `run_id` 和从 1 开始的步号。这样「只有这一个节点产生新记录」（PRD 8 第 5、6、7 条）好验证：新运行里只有这些记录，原运行一条都不加。这三种运行同样按第 1 节检查 adapter 和动作清单。

### 7.1 单跑一个节点

单跑可以不依赖任何旧运行（例如手填输入），所以端点不挂在某次运行下面，用 `POST /api/single-runs/node`。输入从下面四种里选一种：

| 来源 | 请求里写 | 记录里的输入来源 |
|---|---|---|
| 某次运行的记录 | `{"kind": "record", "run_id": ..., "step": ...}` | 缓存 |
| 手动固定的输入 | `{"kind": "pinned"}` | 手动固定 |
| 手填 | `{"kind": "manual", "value": {...}}`，按输入模型校验 | 手填 |
| 默认 | 不写 | 这个节点在最近一次运行里最后一轮的记录，来源记为缓存 |

单跑环里的节点时，用户先在右侧面板的下拉框里选用哪一轮的输入（界面规格第 5 节「运行时间 · 第几轮」），默认最后一轮。请求里 `step` 就是那一轮记录的步号。新记录的输入和那一轮记录一致（PRD 8 第 17 条）。

单跑只执行这一个节点，T2 照常写输出，但不往下游发待执行数据，也不派发别的节点。

测试用 `write` 节点默认模拟；请求带 `"side_effects": "real"` 并且 `confirm` 字段等于服务端在 `GET /api/node-types` 里给的那句确认文字，才真执行（推断：用确认文字防止前端绕过确认框直接发请求）。真执行的幂等键是这次单跑的 `run_id:1`。

`motion` 节点没有模拟，单跑就是让机器人做这个动作。adapter 报 `target = real` 时，请求必须带 `confirm` 等于这个动作的确认文字（推断）。缓存输入的问题：动作的输入常常来自感知节点，例如「杯子在 (x, y, z)」；配置哈希没变就不报过期，可是杯子可能挪了，机器人也不在原处了（调研附录第 5.7 节）。所以单跑 `motion` 节点时，输入来源是缓存的，响应和界面都写出这份输入的采集时间（上游 `read` 输出的 `captured_at`），确认文字里也带上它（推断）。

### 7.2 单跑一组

`POST /api/single-runs/group`，带分组里的节点 id 列表。服务端在锁定版本上把连线分成三类：

- 组内连线：两端都在组里，照常传数据。
- 入口：从组外连进来的线，包括从组外接回来的回边。每个入口在这次运行开始时发一次数据，值来自用户选的那次运行、那一轮的上游记录，或者入口上固定的值（PRD 5.1、界面规格第 7 节）。入口只发一次，所以分组只框了环的一部分时，这一组不会自己循环。
- 出口：从组里连到组外的线，数据写进记录但不派发组外节点。

分组框住了整个环时，环在组内照常循环。这种情况服务端也要按第 1 节的规则检查组内的环有没有出口（出口可以连到组外），没有就拒绝。总步数上限同样生效。分组里有 `motion` 节点的，确认规则同 7.1 节。

### 7.3 输入快照和过期

输入快照就是某条运行记录里的输入，不另存（PRD 第 5 节）。每条记录写一份上游配置哈希 `upstream_hashes`：一个 `{node_id: config_hash}` 的表，范围是锁定版本里所有能沿连线走到这个节点的节点；如果这个节点在环里，范围也包括它自己，因为它后几轮的输入来自它前几轮的输出。

`config_hash` = sha256（节点类型 id、节点类型源码的 sha256、参数的规范化 JSON）。手写节点的源码哈希用 `inspect.getsource(节点类)` 算，节点代码改了哈希就变（推断）。动作节点的类是生成的，源码哈希换成动作说明哈希（第 6 节，推断）。

判断过期时，用记录里的 `upstream_hashes` 和这个流程最新版本里同一批节点的哈希逐个比，有不同就是「可能过期」，不同的节点列表放进响应的 `stale.changed_nodes`，界面悬停时列出来（界面规格第 5 节）。节点在新版本里被删了，也算改了。过期的快照仍然可以用，不自动删（PRD 第 5 节）。

由这个范围直接得出：

- 改了节点 A，过期的是所有从 A 能走到的节点的快照；A 在环里时包括 A 自己。
- 「节点自己的参数改了不影响它的输入快照」只对不在环里的节点成立（PRD 8 第 12 条）。在环里时，改它自己的参数，它后几轮的输入快照会被标成可能过期（PRD 8 第 18.2 条）。

配置哈希只看配置，看不出世界变了。传感器读数和图像另外按采集时间显示（第 2.3 节、7.1 节）。

手动固定的输入不检查过期，只有用户取消固定才失效（PRD 第 5 节）。

### 7.4 固定输入

`PUT /api/flows/{flow_id}/pins/{node_id}/{port}` 固定一个端口的输入，值可以直接给，也可以指向某条记录。`pins` 表跟着流程走，不进流程版本。固定的值只在单跑节点、单跑分组和分组入口上用；整条运行总是用真实上游（界面规格已按这条画）。固定的是图像时，固定的是图像引用；图像已不在内存里，单跑时这一步记出错（第 2.2 节，推断）。

### 7.5 从某个节点重跑

节点出错后，或者想从环中间某一轮接着跑，界面发 `POST /api/runs/{run_id}/rerun-from`，带那个节点那一步的步号 k。服务端开一次 `kind=rerun` 的新运行：

- 起始队列 = 原运行第 k 步之前的待执行数据（第 2.2 节的公式）。
- 各节点的轮次从原运行第 k 步之前的执行次数接着数，所以从环的第 2 轮重跑，新记录从第 2 轮开始（PRD 5.1）。
- 新运行记下 `parent_run_id` 和 `base_step = k - 1`，原运行的记录保留不动。
- 上游节点不重跑，所以上游没有新记录（PRD 8 第 7 条）。
- 测试用 `write` 节点默认模拟，和单跑一样（PRD 3 第 10 步）。用户可以在请求里选真执行。
- `motion` 节点照 7.1 节：真做，`target = real` 时要确认。机器人现在的状态和原运行第 k 步时不一样，重跑前 adapter 读一次状态放进响应，界面先给用户看（推断）。

---

## 8. 服务重启后恢复

存储照 v0.1：记录在 SQLite 里，重启后还在（PRD 第 5 节：运行日志保留）。服务启动时，先连 adapter，再扫 `runs` 表里状态是 `running` 或 `paused` 的运行，逐个恢复：

1. 从 `pending` 表重建待执行队列（未消费的行），从 `steps` 表数出每个节点的轮次和已用步数。节点之间只靠记录传数据，不靠进程内存（PRD 第 5 节），所以这两张表就够了。
2. 找状态还是 `running` 的记录，也就是 T1 提交了、T2 没提交的那一步：
   - `none`、`read` 节点：记录改成「中断」，把它取走的待执行数据恢复成未消费。下次派发时重新执行，新记录拿新步号。
   - `motion` 节点：记录改成「中断 · 结果未知」，恢复待执行数据，不自动重发。再让 adapter 问控制器这个目标现在怎么样（`active_goals()`，按记录里的 `goal_id` 对）。ROS 2 下有两个来源：动作服务端会缓存结果，「The server should cache the result once it is ready so multiple clients have to opportunity to get it.」；状态话题的默认 QoS「should be TRANSIENT LOCAL with a history size of 1」，新订阅者能拿到最新状态（[ROS 2 Actions 设计文档](https://design.ros2.org/articles/actions.html)，2026-10-31 核对；见调研附录第 4 节）。前提是动作服务端没跟着重启，结果的保留时长也没过（推断）。问到的结果写进 `pause.goal_check`：还在执行、已成功、已取消、已中止、查不到。还在执行的，心跳早已断了，adapter 守护进程应该已经发过取消（第 2.5.4 节）；查到还在执行，说明这一层没起作用，界面要明说（推断）。没有 `goal_id` 的（`blocking` 动作且 SDK 不给 id），写「查不到」。然后读一次机器人状态，放进 `pause.readback`。
   - 测试用 `write` 节点：记录改成「中断 · 结果未知」，同样恢复待执行数据，并在这些数据上记下原来的幂等键。下次派发这个节点时，新记录的 `retry_of` 指向原来那一步，幂等键沿用原来的 `run_id:原步号`。外部服务认出这个键，就不会再写一次（第 13 节 A1 测这条）。
3. 运行状态改成 `paused`，暂停原因 `restart`，停在下一个要派发的节点执行前。不自动继续跑，等用户点继续（PRD 第 5 节：重启后停在下一个节点前）。原来停在断点上的运行，恢复后停在同一个位置。停着的是结果未知的 `motion` 节点时，继续要带 `readback_id` 和 `resolution`（第 2.6 节）。
4. 新建 `resume` 事件；推送层从 `changes` 表接着编号。

adapter 连不上时，恢复照做第 1、3、4 步，`motion` 节点照样记「中断 · 结果未知」，`pause.goal_check` 和 `pause.readback` 写「adapter 未连上」，继续和单步置灰，直到 adapter 连上并读回一次（推断）。

界面上显示什么（给 Rams 画，字段都在 `GET /api/runs/{run_id}` 里）：

| 位置 | 显示 |
|---|---|
| 运行记录 | 都在，和重启前一样，可以照常翻看 |
| 顶栏状态词 | 「已暂停 · 服务重启后」（`pause.reason = restart`，界面规格第 1 节已有） |
| 在执行中被打断的动作节点 | 「中断 · 结果未知」，问号图标（界面规格第 3.1 节的 `state-unknown`） |
| 右侧面板（选中这个节点时） | ①发出的目标：动作名、参数、发出时间、`goal_id`、到期时间；②最后一条反馈；③控制器那边的目标状态（`pause.goal_check`）；④adapter 现读的机器人状态和读取时间（`pause.readback`），机械臂相关的动作单独一行写夹爪是否夹着东西；⑤「重新读取」按钮 |
| 可用的操作 | 读取机器人状态：任何时候。重做这个动作、当作已做完：读回时间晚于重启时间并且用户确认看过机器人以后才可用。停止：任何时候。普通的继续和单步：不可用，因为它们会原样重发同一个动作 |

在执行中被打断的 `none`、`read` 节点照 v0.1 显示「中断」，继续时自动重跑；没有在执行中被打断的运行，节点照常显示 `paused_before`。

断线和刷新不是重启：浏览器断开时运行在服务端照常继续或保持暂停（PRD 3 第 14 步，带动作节点时见第 5.1 节），回来后按第 10.2 节补发。

---

## 9. 跑完、出错、超步数

运行有四种结束状态，互相分开，界面用不同的文字：

| 状态 | 什么时候 | 界面（按界面规格） |
|---|---|---|
| `completed` 完成 | 没有就绪节点 | 节点各自显示完成；被 2.1 节规则标过的节点显示跳过；其余没执行的节点保持空闲 |
| `error` 出错 | 某个节点抛异常、`none`/`read` 节点超时、输出不能序列化、动作被控制器拒绝或中止、控制器到期、前提条件不满足 | 出错节点红框、叉、错误摘要；超时写「出错 · 超时」（界面规格第 3、3.1 节） |
| `stopped` 已停止 | 用户点停止 | 顶栏状态词「已停止」；节点按第 2.5.3 节的表 |
| `step_limit` 超出步数上限 | 下一步的步号会超过 `max_steps` | 不在节点上显示。顶栏步数变琥珀色，写「停 · 步数 N / 上限 N」，右侧面板概况写最后一步是哪个节点（界面规格第 3 节） |

节点出错时整次运行停下（推断：不跳过出错节点继续跑，因为下游拿不到输入），待执行数据留在盘上，用户可以修好参数后从这个节点重跑（第 7.5 节）。动作节点出错时，记录里带结果中的实测状态，没有就让 adapter 读一次（推断）。

超时：每个节点类型有 `timeout_s`，执行时套 `asyncio.timeout()`。到点后按副作用类别分开处理（CTO 定）：

- `none`、`read` 节点：这一步记 `error`，错误类型 `timeout`，运行以 `error` 结束，和抛异常一样。
- `motion` 节点：`timeout_s` 每次调用都作为到期时间传给 adapter，Python 这边 60 s 是第二道（第 2.5.4 节）。执行器超时触发，说明控制器没按时报结果：先调取消（第 2.5.3 节，`cancel_trigger = timeout`），再读状态；这一步记 `interrupted_unknown`，错误类型 `timeout`；它取走的待执行数据恢复成未消费；运行改成 `paused`，原因 `motion_timeout`（推断的名字），停在这个节点执行前，永不自动重试。往下走按第 2.6 节：先读回，再由人选重做、当作已做完或停止。
- 测试用 `write` 节点：照 v0.1。这一步记 `interrupted_unknown`，错误类型 `timeout`；恢复待执行数据，记下原来的幂等键 `retry_idem_key`；运行 `paused`，原因 `write_timeout`；用户点继续时重新派发，新记录的 `retry_of` 指向原来那一步，幂等键沿用 `run_id:原步号`。

同步写法的节点放进线程执行，线程没办法被取消，超时后线程可能还在跑，所以手写节点和 adapter 客户端都写成 `async`（推断）。

步数上限：执行器在派发前检查，`steps_used + 1 > max_steps` 就以 `step_limit` 结束，不写第 `max_steps + 1` 步的记录，也不发下一个动作（PRD 8 第 15 条）。环靠条件节点退出，总步数是唯一的兜底。第一期没有单个环的轮数上限（CTO 定，见第 14 节 b）。

界面上回边只写「第 3 轮」，总步数只在顶栏写「步数 37 / 上限 N」（PRD 3 第 11 步、界面规格第 0 节第 4 条）。服务端在每条 `nodes` 事件里带 `steps_used` 和 `max_steps`。

---

## 10. 协议

指令走普通 HTTP，推送走 SSE（Server-Sent Events：服务端在一条一直不关的 HTTP 响应里一条一条往浏览器写事件，浏览器自带的 `EventSource` 就能接，[MDN](https://developer.mozilla.org/en-US/docs/Web/API/EventSource)）。所有请求和响应都是 JSON，错误统一是 `{"error": "错误码", "message": "给人看的中文", "detail": {...}}`。

### 10.1 HTTP 端点

| 动作 | 方法和路径 | 请求字段 | 成功响应 |
|---|---|---|---|
| 读 adapter 状态 | `GET /api/adapter` | 无 | `{name, target, connected, action_list_version, fetched_at, heartbeat}`。界面节点面板顶上的「来自控制器 · 拉取于 HH:MM」用它 |
| 重新取动作清单 | `POST /api/adapter/refresh` | 无 | 同上；503 `adapter_unavailable` |
| 读机器人状态 | `GET /api/robot/state` | 查询参数 `action?` | `{state, read_at}`，adapter 现读；503 `adapter_unavailable` |
| 列出节点类型 | `GET /api/node-types` | 无 | `{adapter: {...}, types: [{type_id, title, group, side_effect, side_effect_note, confirm_text, timeout_s, inputs_schema, outputs_schema, params_schema, action?}], rejected_actions: [{id, missing_fields}]}`。`group` 是 `control` / `action` / `test`；`action` 是动作说明（第 2.5.1 节） |
| 检查流程里的动作 | `GET /api/flows/{flow_id}/action-check` | 查询参数 `flow_version` | `{action_list_version_locked, action_list_version_now, nodes: {node_id: "ok" / "action_missing" / "action_changed"}}`（推断的端点） |
| 启动运行 | `POST /api/runs` | `flow_id`, `flow_version`, `initial_input`, `max_steps?`（默认 1000，临时）, `client_id`（每个标签页随机生成）, `request_id?`（防重复点击） | 201 `{run_id, flow_version, action_list_version, max_steps, status, last_event_id}`；400 `loop_without_exit` / `invalid_graph`；409 `action_missing` / `action_changed`；503 `adapter_unavailable` |
| 读运行状态 | `GET /api/runs/{run_id}` | 无 | `{run_id, kind, flow_id, flow_version, latest_flow_version, action_list_version, adapter_name, adapter_target, status, stop_requested, max_steps, steps_used, pause, nodes, edges_active, result?, error?, last_event_id}`。`pause` = `{pause_id, reason, node_id, when, step, round, inputs?, robot_state?, readback?, goal_check?}` 或 `null`；`nodes` = `{node_id: {state, round, last_step, duration_ms, error_summary, side_effect_mode, progress?, cancel?}}` |
| 暂停 | `POST /api/runs/{run_id}/pause` | `client_id` | 202 `{status}`。已经暂停时也返回 202 |
| 读回机器人状态（暂停中） | `POST /api/runs/{run_id}/readback` | `client_id` | 200 `{readback_id, state, read_at}`，同时更新 `pause.readback`；503 `adapter_unavailable` |
| 继续 | `POST /api/runs/{run_id}/continue` | `pause_id`, `client_id`, `edits?` = `{inputs?}` 或 `{outputs?}`, `readback_id?`, `resolution?` = `retry` / `mark_done`, `confirm?` | 200 `{status}`；409 `pause_mismatch` / `not_paused` / `awaiting_approval` / `readback_required`；422 改的值校验不过，或 `motion_output_readonly` |
| 单步 | `POST /api/runs/{run_id}/step` | 同继续 | 同继续 |
| 停止 | `POST /api/runs/{run_id}/stop` | `client_id` | 202 `{status, stop_requested: true}`：有动作在执行时，结果由 `cancel_result` 和 `run_finished` 事件给出；没有时 200 `{status: "stopped"}`。已结束时返回 409 `already_finished` |
| 人工确认 | `POST /api/runs/{run_id}/approve` | `pause_id`, `client_id`, `decision`: `approve` / `reject`, `comment?` | 200；409 `pause_mismatch` / `not_paused` |
| 设置断点 | `PUT /api/flows/{flow_id}/breakpoints/{node_id}/{when}` | `mode`: `every` / `round` / `condition`, `round?`, `condition?` = `{field, op, value}`, `enabled` | 200 `{breakpoint}`；422 字段不在输入模型里 |
| 删除断点 | `DELETE /api/flows/{flow_id}/breakpoints/{node_id}/{when}` | 无 | 204 |
| 单跑节点 | `POST /api/single-runs/node` | `flow_id`, `flow_version`, `node_id`, `input?`（见 7.1 节）, `side_effects`: `default` / `real`（只对测试用 `write` 节点有意义）, `confirm?`, `client_id` | 201 `{run_id, input_captured_at?}`；409 `confirm_required`（`motion` 节点且 `target = real` 而没带确认文字）及第 1 节的错误 |
| 单跑分组 | `POST /api/single-runs/group` | `flow_id`, `flow_version`, `node_ids`, `entries?` = `{edge_id: {kind, run_id?, step?, value?}}`, `side_effects`, `confirm?`, `max_steps?`, `client_id` | 201 `{run_id, entries}`；400 组内环没有出口 |
| 从某节点重跑 | `POST /api/runs/{run_id}/rerun-from` | `step`, `side_effects`, `confirm?`, `client_id` | 201 `{run_id, parent_run_id, base_step, robot_state?}` |
| 固定输入 | `PUT /api/flows/{flow_id}/pins/{node_id}/{port}` | `value` 或 `from` = `{run_id, step}` | 200 `{pin}` |
| 取消固定 | `DELETE /api/flows/{flow_id}/pins/{node_id}/{port}` | 无 | 204 |
| 读运行记录 | `GET /api/runs/{run_id}/steps` | 查询参数 `node_id?`, `after_step?`, `limit?`（默认 200，推断） | `{steps: [{step, node_id, round, status, started_at, ended_at, duration_ms, input_source, side_effect_mode, input, output, error, stale: {is_stale, changed_nodes}, pinned, action?}], next_after_step}`。`action` = `{name, goal_id, sent_at, expires_at, last_feedback, final_status, measured_state, cancel, readback}`。大于 64 KB 的输入输出只返回前 200 个字符和 `truncated: true`（推断） |
| 读一条记录 | `GET /api/runs/{run_id}/steps/{step}` | 无 | 一条完整记录，不截断 |
| 读图像 | `GET /api/images/{image_ref}` | 无 | 图像本身；已不在内存里返回 410 `image_gone`（推断） |
| 订阅推送 | `GET /api/runs/{run_id}/events` | 请求头 `Last-Event-ID?`，查询参数 `after?` | `text/event-stream`，见 10.2 |

时间一律用带时区的 ISO 8601 字符串，界面按 Alex 的时区显示。

### 10.2 SSE 推送

每次运行一条事件流。事件格式按 HTML 规范：`event:` 写事件类型，`id:` 写事件编号，`data:` 写一行 JSON，空行结束一条事件（[HTML 规范 9.2.5、9.2.6](https://html.spec.whatwg.org/multipage/server-sent-events.html#parsing-an-event-stream)）。

事件编号：每次运行有一个从 1 开始递增的变更序号 `seq`，存在 `changes` 表里。执行器每改一次状态（节点排队、开始、结束、暂停、继续、断点变更、取消结果、读回）就写一行变更，和对应的记录在同一个事务里。动作的反馈不进 `changes` 表（太多），只在内存里留最后一条，合进下一条 `nodes` 事件的 `progress` 字段（推断）。一条 SSE 事件可能合并了多条变更，它的 `id` 是其中最大的 `seq`。

断线重连：浏览器收到带 `id` 的事件后会记住它；连接断了，浏览器按重连间隔自动重连，并在请求头里带 `Last-Event-ID`（[HTML 规范 9.2.4](https://html.spec.whatwg.org/multipage/server-sent-events.html#the-last-event-id-header)）。服务端收到后从 `changes` 表读 `seq` 大于它的所有变更，按节点合并成一条 `nodes` 事件，再补上最新的 `paused` 或 `run_finished`，立刻发出，然后接着推实时事件。

刷新页面不一样：新页面里的 `EventSource` 是新对象，第一次连接不会带 `Last-Event-ID`（规范只在重连时带）。所以刷新后浏览器先 `GET /api/runs/{run_id}`，拿到 `last_event_id`，再连 `/api/runs/{run_id}/events?after=<last_event_id>`。服务端两者都看，请求头优先。

服务端在流的开头写一行 `retry: 2000`，把重连间隔设成 2 秒（推断）。没有事件时每 15 秒写一行注释 `: ping`，防止中间的代理把空闲连接断掉（HTML 规范 9.2.7 建议每 15 秒左右一行注释）。

合并：执行器每改一次状态就交给推送层，推送层按运行维护一个「脏节点表」，同一节点只留最后状态。每 100 ms 最多发一条 `nodes` 事件（PRD 第 5 节）。下面四种情况立刻发出未发的那批，不等窗口：运行暂停、碰到断点、单步结束、运行结束。之后紧跟 `paused` 或 `run_finished`。运行记录照样每步一条，合并只发生在推送层。

一个订阅者读得慢、积压超过 50 条事件时，服务端丢掉它的积压，改发一条 `snapshot`（推断）。

事件类型：

| `event:` | 什么时候发 | `data` 字段 |
|---|---|---|
| `snapshot` | 连上时没有可补发的起点，或者订阅者积压太多 | 和 `GET /api/runs/{run_id}` 的响应相同 |
| `nodes` | 每 100 ms 窗口一条，或被立刻发出 | `run_id`, `seq_from`, `seq_to`, `steps_used`, `max_steps`, `nodes`: `[{node_id, state, round, step, duration_ms?, error_summary?, side_effect_mode?, input_source?, progress?}]`, `edges_active`: `[edge_id]` |
| `paused` | 运行停下 | `pause_id`, `reason`, `node_id`, `when`, `step`, `round`, `at` |
| `resumed` | 继续、单步、人工确认被接受 | `mode`: `continue` / `step` / `approve`, `resolution?`, `by_client`, `at` |
| `run_finished` | 运行结束 | `status`: `completed` / `error` / `stopped` / `step_limit`, `steps_used`, `max_steps`, `last_step`, `last_node_id`, `error?`, `at` |
| `cancel_result` | 每次取消有了回应（第 2.5.3 节） | `node_id`, `step`, `cancel_called`, `cancel_response`, `goal_final_status?`, `at` |
| `readback` | adapter 读回了一次状态 | `readback_id`, `node_id?`, `state`, `read_at` |
| `adapter_status` | adapter 连上、断开、动作清单变了 | `connected`, `action_list_version`, `at` |
| `breakpoints` | 断点增删改 | `flow_id`, `changed`, `removed` |
| `version_drift` | 这个流程保存出了比运行锁定版本更新的版本 | `locked_version`, `latest_version` |
| `storage_error` | 记录写不进去 | `message`, `at` |

`state` 的取值：`idle`、`queued`、`running`、`paused_before`、`paused_after`、`done`、`error`、`skipped`，对应界面规格第 3 节；`stopped`、`interrupted`、`interrupted_unknown`、`awaiting_approval`，对应界面规格第 3.1 节。「取消被拒」不是单独的 `state`：节点仍是 `running`，带 `cancel.cancel_response = rejected`，界面据此在状态词后加「· 取消被拒」。各种原因的暂停也不是单独的 `state`：节点显示 `paused_before`，界面按 `paused` 事件的 `reason` 选词。「动作已不存在」是开跑前的检查结果，不在这个列表里（第 6 节）。

连接数：浏览器在 HTTP/1.1 下每个域名最多同时开 6 条 SSE 连接，所有标签页合计（[MDN EventSource](https://developer.mozilla.org/en-US/docs/Web/API/EventSource) 的警告）。前端只给当前显示的那次运行开一条连接，切走就 `close()`。

---

## 11. 存储结构

一个 SQLite 文件 `runs.db`，放在控制器上的数据目录（推断：默认 `~/.agent-flow/`）。图像和传感器原始数据不进数据库，也不写成文件（第 2.2 节）。

```sql
CREATE TABLE flow_versions (
  flow_id TEXT, version INTEGER, graph_json TEXT, graph_sha256 TEXT, created_at TEXT,
  action_list_version TEXT, action_hashes_json TEXT,   -- {动作名: 动作说明哈希}
  PRIMARY KEY (flow_id, version));

CREATE TABLE runs (
  run_id TEXT PRIMARY KEY, kind TEXT,            -- full / node / group / rerun
  flow_id TEXT, flow_version INTEGER,
  parent_run_id TEXT, base_step INTEGER,
  adapter_name TEXT, adapter_target TEXT, action_list_version TEXT,
  status TEXT,                                   -- running / paused / completed / error / stopped / step_limit
  stop_requested INTEGER,
  pause_json TEXT, max_steps INTEGER, steps_used INTEGER,
  side_effects TEXT, created_at TEXT, finished_at TEXT, error_json TEXT, result_json TEXT);

CREATE TABLE steps (
  run_id TEXT, step INTEGER, node_id TEXT, round INTEGER,
  status TEXT,          -- running / done / error / stopped / interrupted / interrupted_unknown
  input_json TEXT, input_source TEXT, input_ref_json TEXT, edited_json TEXT,
  output_json TEXT, error_json TEXT,
  started_at TEXT, ended_at TEXT,
  config_hash TEXT, upstream_hashes_json TEXT, input_sha256 TEXT,
  side_effect TEXT, side_effect_mode TEXT,       -- real / mock / cache
  idem_key TEXT, retry_of INTEGER,
  action_name TEXT, return_mode TEXT, goal_id TEXT, sent_at TEXT, expires_at TEXT,
  last_feedback_json TEXT, final_status TEXT, measured_state_json TEXT,
  cancel_json TEXT,     -- 第 2.5.3 节的字段，每次取消一条，存成列表
  readback_json TEXT, resolution TEXT,           -- retry / mark_done
  PRIMARY KEY (run_id, step));
CREATE INDEX steps_by_node ON steps (run_id, node_id, round);

CREATE TABLE pending (
  run_id TEXT, item_id INTEGER, edge_id TEXT, to_node TEXT, to_port TEXT,
  payload_json TEXT, produced_step INTEGER, consumed_step INTEGER, retry_idem_key TEXT,
  PRIMARY KEY (run_id, item_id));

CREATE TABLE changes (
  run_id TEXT, seq INTEGER, at TEXT, kind TEXT, node_id TEXT, payload_json TEXT,
  PRIMARY KEY (run_id, seq));

CREATE TABLE breakpoints (
  flow_id TEXT, node_id TEXT, "when" TEXT, mode TEXT, round INTEGER, condition_json TEXT, enabled INTEGER,
  PRIMARY KEY (flow_id, node_id, "when"));

CREATE TABLE pins (
  flow_id TEXT, node_id TEXT, port TEXT, value_json TEXT, from_run TEXT, from_step INTEGER, pinned_at TEXT,
  PRIMARY KEY (flow_id, node_id, port));

CREATE TABLE commands (
  run_id TEXT, cmd_id INTEGER, client_id TEXT, kind TEXT, pause_id TEXT, at TEXT, result TEXT,
  PRIMARY KEY (run_id, cmd_id));
```

测试用工具 `append_note`、`read_note` 用的笔记库是另一个文件 `demo_notes.db`（推断的文件名，同在数据目录），执行器不读写它：

```sql
CREATE TABLE note_lines (
  note_id TEXT, line_no INTEGER, line TEXT, idem_key TEXT UNIQUE,
  PRIMARY KEY (note_id, line_no));
CREATE TABLE idem_keys (
  idem_key TEXT PRIMARY KEY, result_json TEXT, at TEXT);
```

流程定义和运行记录分开存（PRD 第 5 节）：流程文件是用户保存的那份文件，`flow_versions` 只是服务端为了锁定版本留的副本。

---

## 12. 失败模式

| 情况 | 在哪一步断 | 用户看到什么 | 系统怎么处理 |
|---|---|---|---|
| 节点抛异常 | `run()` 里，T1 已提交 | 节点红框、叉、一行错误摘要；顶栏回到「运行」 | T2 写状态 `error` 和异常类型、消息、堆栈；运行以 `error` 结束；待执行数据保留，可以从这个节点重跑 |
| `none`/`read` 节点超时 | `asyncio.timeout()` 到点 | 同上，摘要写「超时（60 s）」 | 同上，记 `error`，错误类型 `timeout` |
| 动作被控制器拒绝、中止，或前提条件不满足 | 发目标时或结果里 | 节点出错，摘要写控制器给的原因 | 记 `error`，错误类型 `goal_rejected` / `aborted` / `precondition_failed`，带实测状态；运行以 `error` 结束 |
| 动作到了控制器的到期时间 | 控制器那边 | 节点出错，摘要写「控制器到期停下」（推断的文案） | 记 `error`，错误类型 `expired`，带实测状态（第 2.6 节） |
| 动作在执行器超时前没报结果 | 执行器的 `asyncio.timeout()` 到点 | 节点「中断 · 结果未知」，运行暂停 | 调取消、读状态；记 `interrupted_unknown`；运行 `paused`，原因 `motion_timeout`；继续要读回加人确认（第 2.6、9 节） |
| 点停止，动作取消被拒 | 取消的回应 | 节点「运行中 · 取消被拒」，顶栏「停止被拒」，旁边「机器人仍在执行，需要急停请用硬件」 | 记取消回应；不再派发节点；动作自己结束后按结果记，运行 `stopped`（第 2.5.3 节） |
| 点停止，取消没回应或读不到状态 | 取消或读回 | 节点「中断 · 结果未知」 | 运行 `stopped`；记录里写清楚哪一步没回应 |
| adapter 守护进程断开 | 动作执行中 | 节点「中断 · 结果未知」，运行暂停，节点面板「未连上机器人控制器」 | 运行 `paused`，原因 `adapter_lost`；adapter 重连并读回前，继续、单步不可用 |
| 后端进程卡死（事件循环不动） | 任意 | 页面「重连中」，或一直停在旧状态 | 心跳停，adapter 守护进程取消还在执行的目标；控制器到期兜底；`expiry = adapter_only` 的动作只靠前一层（第 2.5.4 节） |
| 输出不能序列化成 JSON | T2 序列化时 | 同节点出错，摘要写「输出不能序列化成 JSON」 | 记 `error`；这是节点代码的错，要在节点评审里查 |
| 动作节点执行到一半服务崩溃 | T1 和 T2 之间 | 重启后见第 8 节的界面表 | 第 8 节：记「中断 · 结果未知」，问控制器目标状态，读回状态，暂停原因 `restart`；不自动重发 |
| 测试用 `write` 节点执行到一半服务崩溃 | T1 和 T2 之间 | 重启后节点显示「中断 · 结果未知」，运行停在这个节点执行前，「已暂停 · 服务重启后」 | 第 8 节：恢复待执行数据，记下原幂等键；用户点继续时带原幂等键重试 |
| 服务崩溃（其他节点或暂停中） | 任意 | 重启后运行停在下一个节点执行前 | 第 8 节；不自动继续 |
| 控制器断电（后端、adapter、控制程序一起没了） | 任意 | 来电重启后同「服务崩溃」 | 画布不对断电那一刻机器人怎么做作任何承诺；这要厂商出处，见第 13.1 节 |
| SSE 断线 | 推送层 | 顶栏连接状态显示「重连中」，节点状态停在断线前 | 浏览器自动重连并带 `Last-Event-ID`；服务端补发合并后的变更。带动作节点的运行，所有页面断开超过时限后暂停，原因 `operator_lost`（第 5.1 节） |
| 浏览器刷新 | 页面 | 页面重新加载，显示和刷新前一样的状态 | 先 `GET` 运行状态，再带 `after` 订阅（PRD 8 第 4 条） |
| SQLite 写失败 | T1 或 T2 提交时 | 顶栏下方出现「运行记录写不进去：<错误>。运行已暂停。」，继续按钮可用 | 遇到 `SQLITE_BUSY` 等待后重试（`busy_timeout` 5000 ms，推断）；其他错误（磁盘满、只读）就停住运行，暂停原因 `storage_error`，推 `storage_error` 事件。T1 失败时节点没执行，动作也没发；T2 失败时把输出留在内存里，用户点继续时先重写 T2，写成功才派发下一个 |
| 开跑时动作不在了或定义变了 | 第 1 节第 2 步 | 点运行被拒绝，节点上「动作已不存在」，顶栏旁「有 N 个节点的动作已不存在」 | 409 `action_missing` / `action_changed`；不建运行，不发任何目标 |
| 动作说明缺必填字段 | `list_actions()` 时 | 节点面板里一行错误「<动作名>：缺 <字段>」 | 不生成这个节点类型；`rejected_actions` 列出 |
| 环没有出口 | 启动运行时 | 点运行被拒绝，提示「这个环没有条件判断节点作出口」并高亮环里的节点 | 第 1 节第 4 步，返回 400 |
| 环有出口但条件一直不成立，跑到步数上限 | 派发第 `max_steps + 1` 步前 | 顶栏步数变琥珀色「停 · 步数 1000 / 上限 1000」 | 以 `step_limit` 结束，不写下一步记录，不发下一个动作 |
| 停在人工确认时点了继续或单步 | 指令到达时 | 运行仍停着，人工确认的提示条还在 | 返回 409 `awaiting_approval`，不放行，不换 `pause_id`；指令照样记进 `commands` 表 |
| 结果未知时点了继续或单步，没带读回 | 指令到达时 | 运行仍停着 | 返回 409 `readback_required`，不发目标 |
| 两个标签页同时操作同一次运行 | 继续、单步、人工确认 | 两个页面都收到同一串事件。后点的那个页面显示「另一个页面已经继续了这次运行」 | 这几条指令必须带 `pause_id`，先到的用掉，后到的返回 409 `pause_mismatch`。所有指令记进 `commands` 表，带 `client_id` |
| 上游改图导致快照过期 | 读记录或单跑时 | 入口端口写「缓存 · 上游已改」 | 按第 7.3 节比较哈希；快照照样能用 |
| 运行中改画布 | 保存出新版本时 | 提示条「画布已改，这次运行仍按 v12」和「从新版本重跑」 | 运行按锁定版本继续；推 `version_drift` |
| 合并节点等不到另一路 | 运行末尾 | 运行完成，合并节点保持空闲；右侧面板概况写「有 N 条数据没被下游取走」（推断） | 没有就绪节点就结束；剩下的待执行数据留在盘上 |
| 浏览器 SSE 连接数用完 | 开第 7 个标签页时 | 新页面一直显示「重连中」 | 前端只给当前显示的运行开连接（第 10.2 节） |

---

## 13. 可证伪的验收

每条写成「做什么 → 应该看到什么」，并尽量配一个「拿掉某个机制就会失败」的反例，用来证明测试本身有效。编号 A1 起；括号里是对应的 PRD v1.4 草稿第 8 节条目。A1–A22 照 v0.1 不变（PRD 第 9 节：存储和重启照 v0.1，A18、A20 保持原样），其中用到 `append_note` 的都是测试用节点。A23 起是 v0.2 新加的，用假 adapter 测；需要真控制器的放在 13.1 节。

| 编号 | 测什么 | 怎么测 | 通过条件 | 反例（拿掉机制后应失败） |
|---|---|---|---|---|
| A1 | `write` 节点崩溃后不重复写（8） | 流程里放 `append_note`。在它的事务 `COMMIT` 之后、执行器 T2 提交之前，用测试钩子让服务进程 `kill -9`。重启，确认运行停在这个节点前、状态「中断 · 结果未知」，点继续跑完 | `note_lines` 里那一行只有一份；台账里这个幂等键一条 | 把幂等键换成每次随机生成：重启后同一行出现两份 |
| A2 | 待执行队列落盘（18.1） | 一个出口在第 3 轮成立的环。在第 2 轮回边发出数据、下游还没派发时 `kill -9`，重启后继续 | 环内每个节点正好 3 条记录，轮次 1、2、3；最终输出和不重启的那次逐字节相同 | 去掉 `pending` 表，只从 `steps` 推断队列：重启后找不到在途数据，环少跑一轮，或运行直接「完成」 |
| A3 | 重启后不自动继续（11） | 停在断点时重启服务，等 5 秒 | 运行状态 `paused`，原因 `restart`，5 秒内没有新记录；点继续后跑完，结果和不重启时一致 | 恢复时直接 `resume.set()`：5 秒内出现新记录 |
| A4 | 推送合并（19.1） | 用 `flow-lib-eval` 第 3 节的图（500 节点、549 条边），换成真实 Python 后端和一个只 `await asyncio.sleep(0)` 的测试节点类型，让节点每秒切 10 次状态（推断：用这个节点类型能在本机产生足够快的状态变化）。浏览器里按秒数 `nodes` 事件，同时用 `spike/run_phases.py` 的方法记帧间隔和长任务 | 每秒收到的 `nodes` 事件不超过 10 条；在同一台机器、同一个 headless Chrome 环境下，帧时间和 Torvalds 的 Vue Flow `lod=keep` 结果一致：各阶段 p95 16.7–16.8 ms，「两者同时」阶段最长一帧不超过 33.4 ms，长任务 0 次（`flow-lib-eval` 第 6 节表格）。正式阈值等 Torvalds 在 Alex 的电脑上重测（PRD 第 9 节） | 关掉合并、每次状态变化发一条：每秒事件数远超 10 |
| A5 | 停下时不等窗口（19.2） | 服务端在写完暂停状态时记 `at`（单调时钟换算成墙钟），浏览器在收到 `paused` 后的第一帧 `requestAnimationFrame` 里记时间，两边在同一台机器上。断点、单步、用户暂停各测 20 次 | 每次的差都不超过 100 ms；p95 不超过 20 ms（推断，用来区分「立刻发」和「等窗口」） | 去掉立刻发出：差值大致均匀分布在 0–100 ms，p95 接近 100 ms，第二个条件失败 |
| A6 | 断线补发 | 运行中用一个本机 TCP 代理切断 SSE 连接 3 秒再放行 | 浏览器重连请求头里有 `Last-Event-ID`；重连后前端的节点状态表和 `GET /api/runs/{run_id}` 的 `nodes` 完全相同 | 服务端忽略 `Last-Event-ID`：断线期间结束的节点在前端停在「运行中」 |
| A7 | 刷新不丢（4） | 停在断点时刷新页面 | 仍停在同一节点，输入输出都在，`pause_id` 不变 | — |
| A8 | 两个标签页 | 两个客户端用同一个 `pause_id` 同时发「继续」，重复 50 次 | 每次恰好一个 200、一个 409；每次只多出这一轮该有的记录 | 去掉 `pause_id` 比对：出现两个 200，或者单步走了两步 |
| A9 | 断点和记录（2、16、16.1） | 按 PRD 第 2、16、16.1 条做 | 和 PRD 写的一致 | 把执行前断点挪到 T1 之后：第 2 条失败（第 3 个节点多一条记录） |
| A10 | 改值继续（3） | 执行前改输入、执行后改输出，各测一次 | 下游记录收到改后的值；被改那条记录的 `edited_json.original` 是原值 | — |
| A11 | 步数上限和出错分开（15） | 出口永远不成立的环，`max_steps` 设 10 | 第 10 步后停止，状态 `step_limit`，不是 `error`；没有第 11 步的记录；`run_finished.status` 是 `step_limit` | 把检查放到派发之后：出现第 11 步记录 |
| A12 | 单跑 `write` 默认模拟（8） | 单跑 `append_note`；再单跑 `read_note` | `note_lines` 没有新行，台账没有新键，记录 `side_effect_mode = mock`；`read_note` 记录是 `cache` | 去掉单跑时的模拟分支：`note_lines` 多一行 |
| A13 | 环里单跑和分组（5、6、17） | 选第 2 轮输入单跑环里一个节点；再框住环里 3 个节点中的 2 个跑分组 | 单跑：新记录输入和第 2 轮那条一致，原运行没有新记录。分组：这 2 个节点各 1 条记录，不循环 | 把从组外接回的回边当组内连线：分组一直循环到步数上限 |
| A14 | 快照过期（12、18.2） | 按 PRD 第 12、18.2 条做 | 和 PRD 写的一致 | `upstream_hashes` 里不收环里的节点自己：18.2 失败 |
| A15 | 记录写不进去 | 运行中把数据库文件所在目录改成只读（或用测试钩子让下一次提交抛 `sqlite3.OperationalError`） | 运行停住，原因 `storage_error`，界面出现提示；恢复可写后点继续，跑完且记录完整 | 忽略写失败继续跑：出现执行了但没有记录的节点 |
| A16 | `none`/`read` 节点超时 | `read` 类的测试节点 `sleep` 比 `timeout_s` 长 | 节点 `error`、错误类型 `timeout`，运行以 `error` 结束 | 把超时当成 `write` 处理：运行停在 `paused` 而不是结束 |
| A17 | 没有出口的环（18） | 按 PRD 第 18 条做 | 400 `loop_without_exit`，带环里的节点 id | — |
| A18 | `append_note` 写内容和登记键是一个事务 | 用测试钩子让 `append_note` 在 `INSERT INTO note_lines` 之后、`INSERT INTO idem_keys` 之前 `kill -9` 服务进程。重启，点继续，让它带原幂等键重试；同样的测试在两条 `INSERT` 之后、`COMMIT` 之前再做一次 | 两次都是：`note_lines` 里这一行只出现一次，`idem_keys` 里这个键一条；运行跑完 | 把两条 `INSERT` 拆成两个事务（先提交内容，再登记键）：第一种杀法重启重试后这一行出现两次 |
| A19 | 人工确认不能被继续绕过（13） | 运行停在人工确认上，用当前的 `pause_id` 发 `continue`，再发 `step` | 两次都是 409 `awaiting_approval`；没有新的 `steps` 记录；`pause_id` 不变；之后 `approve` 用同一个 `pause_id` 返回 200 | 去掉 `reason` 检查：`continue` 返回 200，人工确认的下游两路都没收到数据，或者运行直接结束 |
| A20 | `write` 节点超时记结果未知 | 让 `append_note` 的测试版本在 `COMMIT` 之后 `sleep` 超过 `timeout_s` | 这一步 `interrupted_unknown`、错误类型 `timeout`；运行 `paused`，原因 `write_timeout`；点继续后跑完，`note_lines` 里这一行只有一份 | 超时一律记 `error` 后从这个节点重跑（新幂等键）：这一行出现两份 |
| A21 | 驳回后立刻标跳过（13） | 图：人工确认 →「通过」→ X → 合并；人工确认 →「驳回」→ Y → 合并；合并 → Z。另在「驳回」后接一个耗时 2 秒的节点 W。停在人工确认时点「驳回」 | `approve` 返回后、W 执行完之前，SSE 已推出 X 为 `skipped`；合并和 Z 不是 `skipped`；Y、W 正常执行 | 恢复「运行结束才标跳过」：W 执行期间 X 还是 `idle` |
| A22 | 环里的人工确认不误标 | 环：想一步 → 条件判断「完成了吗」，「是」→ 输出甲（环的出口），「否」→ 人工确认；人工确认「驳回」→ 回到想一步，「通过」→ 输出乙。测试节点让条件判断前两轮都判「否」。第 1 轮点「驳回」，第 2 轮点「通过」 | 第 1 轮驳回后输出乙不是 `skipped`；第 2 轮通过后输出乙执行 | 计算集合 A 时不沿回边走：第 1 轮驳回后「输出」被标跳过 |
| A23 | 心跳：后端死了 adapter 取消目标（T2） | ROS 2 型假 adapter：动作执行中 `kill -9` 后端 | 守护进程在 `heartbeat.timeout_s` 内调取消，目标进入 CANCELED（5） | 去掉心跳：目标仍是 EXECUTING（2），复现 [rclcpp#2241](https://github.com/ros2/rclcpp/issues/2241) |
| A24 | 控制器到期（T1） | `expiry = controller` 的假动作：同时杀掉后端和守护进程 | 机器人（假控制器）在 `expires_at` 停下；Spot 类接口上反馈 `STATUS_COMMAND_TIMED_OUT` | 不传到期时间：动作一直执行到自己结束 |
| A25 | 每个可取消动作真能取消（T3） | 对每个 `cancellable = true` 的动作发一次取消 | 回应 `ERROR_NONE`（0），目标经 CANCELING 到 CANCELED | 用 rclpy 默认取消回调的服务端应得到 `ERROR_REJECTED`（1）（[rclpy server.py](https://github.com/ros2/rclpy/blob/rolling/rclpy/rclpy/action/server.py)） |
| A26 | 停止调取消、不发新动作（T6） | 执行 `motion` 节点时点停止 | 记录里 `cancel_called = true`、有 `cancel_response` 和读回状态；adapter 日志里停止之后没有新目标 | 只取消 asyncio 任务：假控制器上目标仍在执行 |
| A27 | 取消被拒 | 构造一个拒绝取消的假动作，点停止 | 节点「运行中 · 取消被拒」，顶栏「停止被拒」；不再派发节点；动作结束后运行 `stopped` | 把拒绝当成功：节点立刻显示「已停止」 |
| A28 | 动作超时不自动重试 | 假动作永远不报结果 | 60 s（或放大后的值）后节点「中断 · 结果未知」，运行 `paused`，原因 `motion_timeout`；不带 `readback_id` 继续返回 409 `readback_required`；adapter 收到的目标数仍是 1 | 自动重试：人操作前目标数已是 2 |
| A29 | 重启时动作在执行 | 假动作执行中 `kill -9` 后端再启动 | 运行 `paused`，原因 `restart`；节点「中断 · 结果未知」；`pause.goal_check` 有值；5 s 内 adapter 没收到新目标 | 照 `none` 节点处理：重启后自动重发 |
| A30 | 动作已不存在（T10） | 从假 adapter 删掉一个动作，或改它的参数 schema，再点运行 | 409 `action_missing` / `action_changed`，没有任何记录，没有任何目标 | 派发时才检查：前面的节点已经跑了 |
| A31 | 动作说明缺字段 | 假 adapter 报一个缺 `timeout_s` 的动作 | 不生成节点类型，`rejected_actions` 里列出缺的字段 | 不检查：节点出现在面板里 |
| A32 | 先返回的动作等结果才算做完 | `goal_progress` 假动作，挂执行后断点 | 节点保持运行中直到结果回来，断点之后才命中；进度写进 `progress` | 把发出目标当成做完：下游在动作还在执行时就被派发 |
| A33 | 页面断开（T11） | 运行中关掉所有页面 | 10 s 后运行暂停，原因 `operator_lost`，正在执行的节点没被取消；刷新页面（短于 10 s）不触发 | 去掉这条规则：运行继续派发 |
| A34 | 控制器断开 | 运行中让假 adapter 不回探测 | 3 s 后运行暂停，原因 `adapter_lost`，顶栏「已暂停 · 机器人控制器断开」 | 不探测：运行一直停在运行中 |
| A35 | 动作输出不能改（T12） | 停在 `motion` 节点的执行后断点，提交 `edits.outputs` | 422 `motion_output_readonly` | 允许改：下游收到改过的值 |
| A36 | 图像不进库 | 跑相机节点 10 次 | `runs.db` 里没有图像字节，只有宽、高、字节数、`captured_at`；进程重启后读图返回 410 `image_gone` | 写图像：`runs.db` 变大约 10 张图的大小 |
| A37 | 不绑局域网就不开放；绑了要令牌 | 默认启动，从另一台机器连；再用 `--host 0.0.0.0` 启动，不带令牌请求 | 第一种连不上；第二种返回 401 | 不检查令牌：返回 200 |

### 13.1 接真机前的 adapter 验收

v1 用假 adapter，下面这些不卡 v1（PRD 第 7 节，Tedrake）。数字只取出处里的；没出处的写「用 adapter 声明的值」。

| 编号 | 测什么 | 通过条件 | 出处 |
|---|---|---|---|
| R1（T4） | Spot 类接口：E-Stop 端点 `timeout` 配成 9 s（Spot 教程示例值），停止签到 | 9 s 后机器人进入 SETTLE_THEN_CUT（坐下） | [Spot 编程入门](https://dev.bostondynamics.com/docs/python/understanding_spot_programming)、[Spot 协议参考](https://dev.bostondynamics.com/protos/bosdyn/api/proto_reference)（调研附录 T4） |
| R2（T5） | 话题式速度控制（只在用 ros2_controllers 一类控制器时）：停发速度指令 | 0.5 s 后速度置零（默认 `cmd_vel_timeout`）；四足控制器要换成它自己的出处 | [diff_drive_controller 参数](https://github.com/ros-controls/ros2_controllers/blob/master/diff_drive_controller/src/diff_drive_controller_parameter.yaml) |
| R3（T13） | 硬件急停后复位 | 画布上的运行保持暂停，不自动继续 | ISO 13850 第 4.1.1.2 条「The reset shall not initiate machine start up」（调研附录第 6.1 节） |
| R4 | 停发指令后狗怎么做 | 和厂商文档写的一致 | 接真机前必须给厂商出处（现在没有） |
| R5 | 控制器断电后狗怎么做；断电时硬件急停照样可用 | 同上 | 接真机前必须给厂商出处（现在没有） |
| R6 | 夹爪断电后能不能夹住 | 同上；`cancel.after` 和 `read_state` 按原文写 | 接真机前必须给厂商出处（现在没有） |

---

## 14. 给 Alex 的两个决策

### (a) 总步数上限的默认值

当前临时决定：做成每次运行都能改的参数，默认 1000。这是 CTO 自己定的，没有出处，等 Alex 定（PRD 5.1、第 10 节）。

参考 LangGraph 的同类上限 `recursion_limit`（已核实，2026-10-31）：
- 官方文档：从 1.1.4 起默认 10,007 步；1.0.6 到 1.1.3 默认 10,000 步；超限时抛 `GraphRecursionError`（[Graph API · Recursion limit](https://docs.langchain.com/oss/python/langgraph/graph-api#recursion-limit)，[GRAPH_RECURSION_LIMIT](https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT)）。
- 源码：langgraph 1.2.19（PyPI 当前版本）的 `langgraph/_internal/_config.py` 里 `DEFAULT_RECURSION_LIMIT` 取环境变量 `LANGGRAPH_DEFAULT_RECURSION_LIMIT`，没设时是 `"10007"`；`langgraph/errors.py` 里 `class GraphRecursionError(RecursionError)`（[_config.py@1.2.19](https://github.com/langchain-ai/langgraph/blob/1.2.19/libs/langgraph/langgraph/_internal/_config.py)，[errors.py@1.2.19](https://github.com/langchain-ai/langgraph/blob/1.2.19/libs/langgraph/langgraph/errors.py)）。
- 不能直接照搬：LangGraph 数的是 super-step（同一轮里并行执行的一批节点算一步），我们数的是单个节点的执行次数。同一张图，我们的步数大于等于 LangGraph 的步数（推断，由两种计数方式推出）。

| 选项 | 在哪一步出问题 |
|---|---|
| 1000（临时值） | 正常但很长的 agent 环会被误停。以「想一步 → 调工具 → 判断是否完成 → 回到想一步」为例，每轮 3 步（推断：PRD 3 第 11 步的例子），1000 步约 333 轮，加上环外节点会少一些。这个量对调试用的流程够不够，要看 Alex 实际的流程 |
| 照 LangGraph 取 10,007 或 10,000 | 环跑飞时，停下前模型调用节点会执行几千次（按上面的例子约 3,300 次，推断），费用和运行记录都会涨；顶栏要到很晚才变琥珀色 |
| 更小（例如 100，推断） | 跑飞时停得快，但正常的长环也常被误停，用户要频繁改上限 |

推荐：第一期保持 1000，参数每次运行可改。理由：这是调试工具，跑飞时早停比晚停代价小；误停时状态是「超出步数上限」而不是出错，用户把上限调大后从某节点重跑即可，前面的记录不丢。请 Alex 给出他最长的真实流程大概要跑多少轮，再定正式值。

### (b) 要不要给单个环加「最多 N 轮」

当前临时决定：第一期不做（PRD 5.1、第 10 节，CTO 定）。这里写成第二期可选功能，供 Alex 判断。

| 选项 | 在哪一步出问题 |
|---|---|
| 不加（第一期） | 环只靠条件节点退出，总步数兜底。一个环跑飞时会用掉整次运行的步数，同一次运行里别的环和环后面的节点就没得跑了；报错位置在顶栏（「停 · 步数 N / 上限 N」），不在出问题的那个环上，用户要去右侧面板概况看最后一步是哪个节点，才知道是哪个环 |
| 加（第二期可选） | 每个环的条件节点多一个可选参数 `max_rounds`。条件节点第 `max_rounds` 次执行后条件仍要求回到环里时，运行停下。问题出在用户要给每个环估一个轮数：估小了正常的环被误停，估大了等于没有。另外一个强连通分量里可能有多个条件节点，要规定只看出口那一个（推断） |

如果第二期加，状态要和「超出步数上限」分开：

| | 超出步数上限 | 环超出轮数上限（第二期） |
|---|---|---|
| 运行状态值 | `step_limit` | `loop_round_limit` |
| 触发 | 下一步步号超过 `max_steps` | 某个条件节点执行次数达到它的 `max_rounds`，且这次判定结果仍是回到环里 |
| 显示位置 | 只在顶栏，琥珀色（界面规格第 3 节） | 在那个条件节点上，琥珀色边框加文字「已到最多 N 轮」（Rams 的意见）；顶栏步数不变色 |
| `run_finished` 里 | `status: "step_limit"` | `status: "loop_round_limit"`, `node_id`, `max_rounds` |
| 和出错的区别 | 都不是红色，都不算出错 | 同左 |

推荐：第一期不做，按当前决定。为了第二期加的时候不改存储，第一期现在就做两件小事：条件节点的参数模型里留一个可选字段 `max_rounds`（第一期界面不显示，执行器忽略）；运行状态的取值里留 `loop_round_limit`。第一期用总步数上限加右侧面板概况里「最后一步是哪个节点」，足够定位跑飞的环（推断），等 Alex 用过真实流程后再看要不要加。


---

## 15. 出处（2026-10-31 逐个打开核对过）

v0.1 已有的：

- Python `asyncio.Event`：<https://docs.python.org/3/library/asyncio-sync.html#asyncio.Event>
- Python `asyncio.timeout()`（3.11 加入）：<https://docs.python.org/3/library/asyncio-task.html#asyncio.timeout>
- MDN `EventSource`（含 HTTP/1.1 下 6 条连接的警告）：<https://developer.mozilla.org/en-US/docs/Web/API/EventSource>
- MDN 使用 SSE（`id`、`retry` 字段）：<https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events>
- HTML 规范 SSE，`Last-Event-ID` 请求头：<https://html.spec.whatwg.org/multipage/server-sent-events.html#the-last-event-id-header>
- Pydantic JSON Schema：<https://docs.pydantic.dev/latest/concepts/json_schema/#generating-json-schema>
- SQLite WAL：<https://sqlite.org/wal.html>
- SQLite `PRAGMA synchronous`：<https://sqlite.org/pragma.html#pragma_synchronous>
- Temporal Activity 幂等：<https://docs.temporal.io/activity-definition#idempotency>
- LangGraph checkpointer：<https://docs.langchain.com/oss/python/langgraph/checkpointers>
- LangGraph 重新执行和幂等：<https://docs.langchain.com/oss/python/langgraph/graph-api#re-execution-and-idempotency>
- LangGraph `recursion_limit`：<https://docs.langchain.com/oss/python/langgraph/graph-api#recursion-limit>
- LangGraph `GRAPH_RECURSION_LIMIT`：<https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT>
- LangGraph 源码 1.2.19：<https://github.com/langchain-ai/langgraph/blob/1.2.19/libs/langgraph/langgraph/_internal/_config.py>、<https://github.com/langchain-ai/langgraph/blob/1.2.19/libs/langgraph/langgraph/errors.py>

v0.2 新加的：

- ROS 2 接口（`.action` 文件的 goal / result / feedback 三段）：<https://docs.ros.org/en/jazzy/Concepts/Basic/About-Interfaces.html>
- ROS 2 动作概念（页面有人机验证，按 GitHub 上的源文件核对）：<https://docs.ros.org/en/jazzy/Concepts/Basic/About-Actions.html>
- ROS 2 Actions 设计文档（结果缓存、状态话题 QoS）：<https://design.ros2.org/articles/actions.html>
- REP 103（单位用 SI：米、弧度、秒）：<https://www.ros.org/reps/rep-0103.html>
- rclpy 动作服务端（`default_cancel_callback` 返回 REJECT）：<https://github.com/ros2/rclpy/blob/rolling/rclpy/rclpy/action/server.py>
- rclcpp issue #2241（读取时仍为 open）：<https://github.com/ros2/rclcpp/issues/2241>
- ros2_control controller_manager（SCHED_FIFO 优先级 50）：<https://control.ros.org/rolling/doc/ros2_control/controller_manager/doc/userdoc.html>
- ROS 2 实时内核说明：<https://docs.ros.org/en/jazzy/Tutorials/Miscellaneous/Building-Realtime-rt_preempt-kernel-for-ROS-2.html>
- Spot、Unitree、IEC 60204-1（Schneider Electric 摘要）、ISO 13850 预览、ros2_controllers `cmd_vel_timeout`：链接照抄自调研附录 `embodied-exec-v0.md` 第 12 节，本规格引用处已逐条附上。

本机 Python 3.13.5、SQLite 3.46.1（2026-10-31 在工作机上读取，不代表控制器）。SQLite WAL 文档提到的 WAL 重置 bug 3.51.3 起修复，只在两个以上连接同时写或做检查点时出现；本规格只用一个写连接，打包时最好带 3.51.3 及以上（[WAL 文档第 11 节](https://sqlite.org/wal.html)）。

---

## 16. 未决项（谁来答，答之前按什么做）

| 问题 | 谁答 | 答之前按什么做 | 影响哪几节 |
|---|---|---|---|
| 浏览器在控制器本机还是另一台电脑 | Alex | 默认 `127.0.0.1`；`--host` 绑非回环地址要令牌，否则 401（Torvalds 已实现） | 0.2、A37 |
| 控制器是不是 Jetson Thor；JetPack 7 有没有实时内核 | Alex | 按资源规则跑，不假设有实时内核 | 0.1 |
| 后端内存上限多少 | Torvalds 实测 | 设上限，值待定 | 0.1 |
| 画布停止：停在原地还是回安全位置 | Alex（PRD 建议停在原地） | 停在原地、电机保持供电，不发新动作 | 2.5.3、5 |
| 页面断开 10 s、探测 adapter 3 s、Python 第二道超时 60 s | Torvalds（已实现的默认值，没有出处，可配） | 照默认 | 2.5.4、2.6、5.1、9 |
| 心跳间隔和时限 | adapter 声明 | 不写数字 | 2.5.4、A23 |
| 内存里保留几张图 | Torvalds | 推断：每个运行最近几十张，值待定 | 2.2 |
| 停发指令后狗怎么做、控制器断电后怎么做、夹爪断电能不能夹住 | Tedrake（知道控制器是哪家以后） | 不对这三件事作承诺；接真机前补厂商原文 | 13.1 R4–R6 |
| 手写的控制节点留哪些（含不含「循环」） | Tedrake、Steve | 第 2.4 节的表；环用回边加条件节点 | 2.4 |
| 暂停要不要同时取消正在执行的动作 | Alex | 不取消，做完再停（推断） | 2.6、3 |
| 第 14 节两个决策 | Alex | 1000 步，临时；不加单环轮数上限 | 9、14 |

---

## 和 PRD/界面规格对不上的地方

这些文件我没有改，按原文列出，由 Fadell、Rams 决定改哪边。依据 PRD v1.4 定稿（17:05）和界面规格（17:05）。v0.1 列过的第 5、6 条（`write_timeout`、`awaiting_approval` 的界面处理）界面规格已经补上，已删掉。

1. `flow-lib-eval-2026-10-31.md` 第 5 节：「**2. 验收项 8.13 填什么数字……**」。PRD 里画布性能是第 19 条，不是 8.13。
2. PRD 第 5 节：「只有 JetBrains Mono 放进仓库并记入依赖清单」。`spike/vendor-manifest.json` 里还没有 JetBrains Mono 的条目。
3. PRD 第 5 节：「大文件只存路径和内容哈希（推断）」。v0.2 图像只在内存里，记录只存元数据，不存路径和哈希（第 2.2 节）。这句要改成只对非图像的大输出成立，或删掉。
4. PRD 第 2 节：「不写的是写本地文件的演示工具」，PRD 第 9 节又写 A18、A20 保持原样。本规格保留测试用 `append_note` 和 `demo_notes.db`（第 2.4、11 节），标「仅测试」。两处要统一一句。
5. PRD 第 10 节：「机器人接口第一期不接」。和 PRD 第 7 节的 adapter 规则矛盾，这句是旧的。
6. PRD 第 5 节：「控制器里已没有这个动作」；界面规格第 16 节：「动作已不存在」。本规格用界面规格的词，PRD 要统一。
7. PRD 第 5 节副作用标记只有 `none`、`read`、`write`，`write` 是「写外部，重放默认返回模拟值」。本规格加了 `motion`（没有模拟），`write` 只留给测试节点（第 2.3 节）。PRD 第 8 节第 8 条也按 `write` 写。
8. PRD 第 3 节第 5 步：「write 节点超时记为……」。没写 `motion` 节点超时，本规格记「中断 · 结果未知」，运行暂停（第 9 节）。
9. PRD 第 3 节第 14 步：「关掉或刷新页面，运行在服务端继续或保持暂停」。现在没有页面连着超过 10 s 运行会暂停（第 5.1 节，Torvalds 已实现）。这一步要改。
10. 界面规格第 1 节状态词只有「断点 / 单步 / 手动 / 服务重启后 / 等待确认 / 写入超时 / 记录写不进去」。还差「页面断开」「机器人控制器断开」，以及 `motion_timeout` 的词（建议「动作超时」，推断）。
11. 界面规格第 3.1 节：`interrupted`「只在服务崩溃时出现……重启后继续时自动重跑」，`interrupted_unknown` 只写了 `write` 节点。`motion` 节点被打断记 `interrupted_unknown`，不自动重跑，继续要先读回（第 2.6、8 节）。
12. 界面规格第 16 节：「停止被拒」的顶栏词。本规格运行状态值仍是 `running`，带 `stop_requested = true` 和取消回应，界面据此选词；界面规格要写明对应关系。
13. 界面规格第 16 节手写控制节点列了「循环」，PRD 第 10 节同样。v0.1 起环是回边加条件节点，没有「循环」节点（第 2.4 节）。
14. PRD 第 7 节：「浏览器在另一台电脑上，两者之间走网络」，同一节又写「后端现在只听本机地址」、浏览器在哪待答。PRD 第 8 节第 1 条要求请求只发往本机。绑局域网时要令牌，第 1 条要改成「默认只发往本机」。
15. PRD 第 5 节：「手写表里没写的动作一律当作会动」。本规格对 `side_effect` 是这样；但其他必填字段缺了不生成节点（第 2.5.1 节），PRD 没写这一半。
16. 界面规格第 16 节「运行记录保留」写「重启后怎么显示，等 Thompson 的执行模型定了再改」。已定，见第 8 节的界面表。
