# RPent 调研与接入判断

调研日期：2026-11-12。依据 RLinf 官方文档、公开源码和检查点目录。
下述调研阶段只做静态核对，未安装环境、下载权重、接入模型或运行任务。
这里的“静态核对”是我们对本项目与 RPent 源码、接口和文档的检查，不是 RPent 或 RoboCasa 的评测成绩。

后续实施：用户已于 2026-11-12 授权接入 RoboCasa，当前实现与验证状态见 [接入说明](robocasa.md)。
下文保留调研时的判断，不能将调研结论当作运行证明。

## 结论与候选

**优先考虑复用 RPent 的 RoboCasa365 能力层，作为本框架第一次真实复合操作集成。**
相较从 VLABench 的数据生成技能重新适配，它已有环境客户端、动作原语、策略服务和明确的评测清单；
这是对接入工作量的判断，尚未实测兼容性或完成时间。VLABench 保留为后续候选。

RPent（Recursive Physical Agent）本身也是具身 Agent 框架：高层规划器结合工具反馈与经验，
调用冻结的动作模型及程序化原语。其公开集成包括 LIBERO、RoboCasa、RoboTwin 和 Franka 等；
它提供整套参考系统，而不只是一个 VLA 权重。
来源：[官方仓库](https://github.com/RLinf/RPent)。

| 可复用路线 | 现成能力 | 本项目的定位 |
| --- | --- | --- |
| RoboCasa365 + RLDX-1 | PandaOmron 移动操作、相机、动作原语、策略服务、Target50 清单 | 优先做复合操作与局部移动闭环 |
| RoboTwin 2.0 + LingBot-VLA | 双臂桌面环境、动作接口、50 个 C2R 任务 | 如果坚持固定桌面双臂场景，可优先选择；示例有 `beat_block_hammer` |
| LIBERO / LIBERO-PRO | 已有策略与仿真接入 | 继续作为轻量联调候选，不把单次抓放当主验收 |

RoboTwin 的 C2R 使用干净场景经验，在随机背景、杂物、光照和桌高下评测。
其官方文档提供 Python 3.11、SAPIEN/CUDA/图形依赖与 LingBot 检查点配置；本次未核实该检查点总字节数。
来源：[RoboTwin 集成](https://rpent.readthedocs.io/en/latest/rst_source/simulators/robotwin.html)。

RoboCasa 的 Target50 包括 18 个原子、16 个 Composite-Seen 和 16 个 Composite-Unseen 任务。
建议先用 `OpenDrawer` 检查能力连通，再选 `PrepareCoffee` 或 `LoadDishwasher`，随后加入 `ArrangeTea` 等未见组合。
这些名称来自官方清单；任务完成顺序仍交给 Agent，不能把专家轨迹写进 Runtime。
来源：[Target50 清单](https://github.com/RLinf/RPent/blob/main/robots/robocasa/eval/target50.json)。

## 如何接到我们的框架

保留我们的 Planning、Registry、Hook、Memory、事件与资源管理。
在独立依赖环境中复用 RPent 的环境/策略服务与原语，补一层本项目的 Provider 适配。
**整套 RPent 独立运行已有入口；成为我们的 Provider 仍需开发，不能称为即插即用。**
我方也尚无具体 Planner 模型客户端，完整自主运行还需由应用层注入真实 Planner。

RPent 的 Toolkit 提供 `list_tools()`、`execute_tool(name, input_dict)`，工具声明包含参数与说明；
返回 JSON 数据及 PNG 字节。环境和动作模型服务支持 HTTP RPC。
因此可以复用服务和声明，但需显式补充我方的 domain、资源、输出契约和 Skill 引用。
来源：[官方接口](https://rpent.readthedocs.io/en/latest/rst_source/development/interfaces.html)。

| 我方工具域 | 复用入口示例 | 适配责任 |
| --- | --- | --- |
| State | 环境相机、机器人状态、相机几何 | 保存图像资源引用、观测时间和仿真 step；按需提供给 Planner |
| Execution | `move_to`、`set_gripper`、`navigate_to`、`rldx_skill` | 注册独立能力；关联 call_id，管理资源、取消、执行结果 |
| Verification | `env.check_success`、任务进度或接触证据 | 独立记录目标、结论、证据与关联动作；通信失败不能转成判定失败 |

来源：[环境客户端](https://github.com/RLinf/RPent/blob/main/robots/robocasa/env_client.py)、
[动作原语](https://github.com/RLinf/RPent/blob/main/robots/robocasa/primitives.py)、
[环境服务](https://github.com/RLinf/RPent/blob/main/robots/robocasa/env_server.py)。

适配时有四个已观察到的实际边界：

- **执行权与取消：** Toolkit 采用只读并发/写操作独占，取消依赖处理器检查取消信号并等待清理。
  这不等于我方逐调用、逐资源的停止确认。相同仿真实例的所有推进操作应共用一个执行资源，
  不能仅按“底盘/机械臂”拆锁后并发调用同一个 `env.step`。取消确认前继续持有占用。
  长动作期间的 State 可用性也需验证，不能把 Toolkit 的只读标记理解为可与写操作同时运行。
  来源：[Toolkit 调度与取消实现](https://github.com/RLinf/RPent/blob/main/rpent/tools/toolkit.py)。
- **VLA 输入语义：** 当前 RoboCasa 包装使用实时、完整的 `task_language`，覆盖不一致的 prompt；
  因此不能把 `rldx_skill` 宣传为任意子目标指令执行器。其 `rldx_arm` 也允许小幅底盘动作，不能仅占用手臂。
  原语构造会 reset 环境，恢复已有 Session 时不能直接重建并默认续接。
  来源：[RoboCasa 原语实现](https://github.com/RLinf/RPent/blob/main/robots/robocasa/primitives.py)。
- **动作历史：** RLDX 有连续帧与模型会话状态；手动动作介入后需要重新对齐历史。
  复用应保留这层状态管理，不能每次调用都重建策略会话。动作步数耗尽或稳定停止也不等于目标达成。
  来源：[RLDX 闭环实现](https://github.com/RLinf/RPent/blob/main/robots/robocasa/rldx_skill.py)。
- **验证与观察权限：** 仿真成功谓词和数值进度可用于诊断，但需标明真值反馈。
  若要测纯视觉验证，应把谓词留在评测器侧。不能把 RPent 的 `finish` 或工具 `ok` 当成我们的成功证据，
  也不应自动导入其所有文件、结束和 reset 工具。
  来源：[状态记录](https://github.com/RLinf/RPent/blob/main/robots/robocasa/tools.py)、
  [Toolkit 装配](https://github.com/RLinf/RPent/blob/main/robots/robocasa/toolkit.py)。

## 环境与容量

RPent 的 RoboCasa 路线使用独立 Python 3.10 环境、Linux、NVIDIA CUDA/EGL，
安装 `.[robocasa]` 会使用 RLinf 维护的 RoboCasa、robosuite 和 RLDX 分支。
不要与上游 RoboCasa 或 RoboTwin 的依赖要求混用；需要记录实际提交版本。
官方提供无需 Planner 凭据、无需模型权重的真实环境 smoke test，覆盖相机、移动与成功谓词。
它只能证明环境可运行，不能证明 Agent 已完成任务。
来源：[RoboCasa 安装与环境检查](https://rpent.readthedocs.io/en/latest/rst_source/simulators/robocasa.html)。

| 项目 | 核实规模 | 说明 |
| --- | --- | --- |
| 厨房资产 | 约 10 GB | 官方安装估计；评测不需要训练数据集 |
| `RLWRLD/RLDX-1-FT-RC365` | 约 13.8 GB | revision `587e9ecdcc5e7184fcc17f58713908edff5af041` |
| RLDX 基座支持文件 | 约 16.4 MB | 配置与处理器文件，不需要再下载基座权重 |
| 依赖、缓存、轨迹和高层模型 | 另计 | 不能把上述约 24 GB 当总安装空间或运行显存 |

来源：[固定检查点目录](https://huggingface.co/RLWRLD/RLDX-1-FT-RC365/tree/587e9ecdcc5e7184fcc17f58713908edff5af041)、
[官方资源准备](https://rpent.readthedocs.io/en/latest/rst_source/simulators/robocasa.html#installation-and-resources)。
本次未核实端到端最低显存；需在指定 GPU、推理精度与相机设置下实测峰值。
上轮仅在受限执行环境中无法访问 NVIDIA 驱动。2026-11-12 在正常主机权限下已确认 RTX 5090
与驱动可用；实际仿真仍需单独运行检查。

## 导航与评测范围

RoboCasa 可先用 `NavigateKitchen` 检查移动闭环，但其 `navigate_to` 根据世界坐标和当前位姿转向、前进，
源码没有为该原语提供路径规划。它不足以覆盖未知地图、语义寻物、SLAM 和持续多目标导航。
相机 `world_map` 是逐像素世界坐标，不能视为已构建 SLAM 地图。
来源：[底盘控制实现](https://github.com/RLinf/RPent/blob/main/robots/robocasa/primitives.py)、
[相机坐标实现](https://github.com/RLinf/RPent/blob/main/robots/robocasa/env_client.py)。
在核对的公开集成列表中未发现现成 GOAT-Bench/Habitat/Nav2 接口；连续寻物故事仍单独保留 GOAT-Bench 路线。

评测时固定动作模型、观测权限、种子及预算，对比我方框架与 RPent 参考运行。
分别统计通用知识、已有任务经验两种条件，不能只给 RPent 下载任务经验后就声称是无先验自主完成。
其公开 Memory 采用任务与全局经验，远端内容可变化，因此要另存 commit/hash。
探索阶段允许 reset，与正式评测中的原状态恢复分开计数。
来源：[Memory 与探索](https://rpent.readthedocs.io/en/latest/rst_source/guides/memory.html)、
[Target50 协议](https://github.com/RLinf/RPent/blob/main/robots/robocasa/eval/target50.json)。

第一轮依次验收环境连通、独立 Provider 行为、真实 Planner 的复合任务决策。
资源冲突、取消未确认、证据关联和会话恢复仍需单独验证；外部任务成功率不能替代框架鲁棒性检查。
以上为实施建议，未改变本仓库“当前不添加具体模型工具”的范围。
