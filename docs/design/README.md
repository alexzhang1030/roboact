# roboact 设计图（机器人动作版，10-31）
生成：`python3 ../robot.py`，再用 headless Chrome 截 1440×900。动作名、参数、数值都是示例，等假 adapter 动作表。

- r-bp-light / r-bp-dark：抓取前断点，参数表单（单位、范围、「控制器没给范围」、到期琥珀字、停止后状态）
- r-camera：拍照节点，输出标签整张图 + 拍摄时间/轮/步，节点不放缩略图
- r-running：走到运行中 · 12 s（不报进度的动作）
- r-unknown：动作超时，中断 · 结果未知，读回状态 + 重做这一步 / 当作已做完
- r-unknown-reading：同上，读状态中按钮置灰
- r-cancel-rejected：停止被拒
- r-operator-lost：已暂停 · 页面断开（琥珀，可继续）
- r-adapter-lost：已暂停 · 机器人控制器断开（红，继续置灰），真机红框
- r-missing：动作已不存在，节点面板「来自控制器」+ 六个控制节点，运行置灰
- r-token：401 访问令牌整屏
- r-waiting：运行中 · 等待前提条件（exec-model v0.2.1 §2.5.5）
