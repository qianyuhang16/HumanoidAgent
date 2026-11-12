# 用户故事：桌面抓取与导航的可行性评估

> 选型更新：本页的单次抓放和指定点导航降为能力接入冒烟测试。
> 自主 Agent 主评测改为桌面多步操作与连续寻物导航，候选、环境资源及对照设计见
> [自主 Agent 评测选型](agent-evaluation.md)。本页保留原环境估算和架构静态检查供追溯。

评估日期：2026-11-11。范围为任务选型、环境与权重评估、现有代码静态检查及后续验收设计。
没有安装仿真环境、下载模型、添加具体 Provider，也没有实际运行这两个任务。
先使用可复现的仿真环境；真机型号、传感器与控制接口确定后另行评估。
本页记录选型当时的环境估算与接口缺口；后续模型和记忆改动见 [VLM 接入](deepseek.md)与 [Memory 设计](memory.md)。

## 选型与容量

| 项目 | 桌面抓取放置 | 室内指定点导航 |
| --- | --- | --- |
| 环境与能力 | LIBERO-Spatial + OpenVLA 官方微调策略 | TurtleBot3 Burger + Gazebo Harmonic + Nav2 |
| 具体任务 | 抓起盘子与小烤碗之间的黑碗，放到盘子上 | 前往用户在地图中指定的 B 点；处理缺失信息、路径阻塞与取消 |
| 底层权重 | `openvla/openvla-7b-finetuned-libero-spatial`，约 **15.1 GB** | 所选 AMCL + NavFn + DWB 组合的神经网络权重 **0 GB** |
| 软件基线 | Linux、Python 3.10.13、PyTorch 2.2.0、Transformers 4.40.1、flash-attn 2.5.5、robosuite 1.4.1、LIBERO/MuJoCo | Ubuntu 24.04、ROS 2 Jazzy、Gazebo Harmonic、TurtleBot3 jazzy 分支、Navigation2、RViz2 |
| 硬件规划估算 | 单环境、非量化推理：建议 24 GB NVIDIA 显卡、32 GB RAM、8 核 CPU；官方复现实验使用 A100 | 单机器人小场景：建议 4–8 核 CPU、16 GB RAM、支持仿真图形渲染的 GPU；所选导航算法不要求 CUDA |
| 磁盘预留估算 | 40–60 GB：权重、Python/CUDA 依赖、仿真资产、少量日志 | 20–30 GB：ROS/Gazebo、工作空间、场景、少量日志；容器镜像及构建缓存可能增加占用 |
| 训练数据 | 只做推理不需要下载演示训练集 | 不需要训练数据集；需要世界模型、机器人模型和地图 |

权重大小来自官方文件列表；硬件和磁盘预留是工程估算，不是官方最低配置或本机实测。
GB 使用十进制口径，15.1 GB 约为 14.1 GiB；显存还需容纳运行时、激活与渲染，不能按权重文件大小直接判断能否运行。
不以量化后的理论参数字节数冒充现成可下载权重或已验证的运行需求。

抓取权重目录有四个 safetensors 分片，页面显示约 4.93 GB、4.95 GB、4.95 GB、263 MB；
直接使用该完整微调检查点，不额外下载基础 OpenVLA 或全部四套 LIBERO 检查点。
官方提供的四套修改后 RLDS 训练数据总计约 10 GB，推理评估可跳过。
LIBERO 场景资产、任务定义和初始状态仍需准备；没有核实独立资产包的精确大小，不能说环境下载量只有 15.1 GB。
安装需按 OpenVLA 的 LIBERO 复现说明锁定依赖，不直接套用 LIBERO 单独训练说明中的环境版本。
来源：[权重文件](https://huggingface.co/openvla/openvla-7b-finetuned-libero-spatial/tree/main)、
[OpenVLA 复现说明](https://github.com/openvla/openvla#libero-simulation-benchmark-evaluations)、
[仿真依赖](https://github.com/openvla/openvla/blob/main/experiments/robot/libero/libero_requirements.txt)。

导航的 0 GB 仅指底层导航没有神经网络检查点，软件、地图与场景并非零体积。
采用官方配置中的 AMCL 定位、NavFn 全局规划和 DWB 局部控制；不加入学习式感知或视觉语言导航模型。
Jazzy/Harmonic 是已明确配对的版本组合，本次不追逐最新发行版。
来源：[ROBOTIS Jazzy 环境](https://github.com/ROBOTIS-GIT/emanual/blob/master/_includes/en/platform/turtlebot3/quick_start/quickstart_jazzy.md)、
[Gazebo/ROS 配对](https://gazebosim.org/docs/harmonic/ros_installation/)、
[导航实际配置](https://github.com/ROBOTIS-GIT/turtlebot3/blob/jazzy/turtlebot3_navigation2/param/burger.yaml)。

两者都需要另外接入高层 Planner 才能成为本项目的自主 Agent。OpenVLA 是动作策略，Nav2 是导航能力，
均不替代 Planning。高层模型尚未选定：若使用远程服务，本地高层权重为 0；若本地部署，
需另加对应模型权重和推理资源。上表不包含这部分，不能作为完整 Agent 的最终硬件账单。

## 故事一：桌面目标抓取与放置

**用户目标：**“把盘子和小烤碗之间的黑碗拿起来，放到盘子上。如果没放好，检查原因后再决定怎么办。”

选择 LIBERO-Spatial 已存在的
`pick_up_the_black_bowl_between_the_plate_and_the_ramekin_and_place_it_on_the_plate`，
保持原场景、机器人与任务定义，避免一开始把策略的跨域泛化问题误认为框架故障。
任务名称见 [LIBERO 官方任务表](https://github.com/Lifelong-Robot-Learning/LIBERO/blob/master/libero/libero/benchmark/libero_suite_task_map.py)。

环境需要桌面物体、机械臂/夹爪、离屏 RGB 渲染、仿真状态及任务成功判定。
Provider 负责图像裁剪、动作归一化、夹爪方向和仿真步进；高层 Planner 不逐帧输出关节动作。
官方策略评估针对完整抓取放置 episode，不应未经验证就把它拆成可以任意暂停、续接的“抓取”和“放置”两个技能。
参考：[官方执行代码](https://github.com/openvla/openvla/blob/main/experiments/robot/libero/run_libero_eval.py)。

拟接入的能力（名称仅用于描述，不在本次注册）：

| 域 | 能力 | 关键契约 |
| --- | --- | --- |
| State | 当前相机观测、机器人/目标状态 | 时间戳、episode ID、资源引用；图像按需提供给 Planner |
| Execution | 一个有界的抓取放置尝试 | 异步；占用该机械臂与夹爪；返回执行结束原因和进度 |
| Verification | 目标物体是否满足放置条件 | 独立读取当前仿真状态，返回 success/failure/uncertain 和关联 call ID |

第一轮 Verification 使用仿真任务谓词作为评估证据，不新增视觉模型权重。
这只能证明仿真中的目标达成；不能据此声称解决了真实视觉抓取验证。
成功条件应包含目标身份及任务谓词，而不是“机器人动过”或“策略循环结束”。

Skill 描述适用场景、输入准备、证据读取与失败后的可选行为；不要求固定工具顺序。
本检查点未声明本项目示例中的“参考图像相似度启动条件”，不能凭空给它附加一个阈值或假设存在比较模型。
若初始观测已充分，Agent 可以直接尝试；缺少目标信息则先观测或询问。
执行结束但验证失败时，Agent 应依据证据选择重新观测、允许的再次尝试、请求协助或终止。
重试必须承认当前场景已改变；评测程序不能偷偷 reset episode 后宣称是原任务的自主恢复。

建议验收样本：

- 官方初始状态集合中的至少 10 个固定状态，记录状态 ID 和随机种子。
- 提供/不提供初始观测两个条件，检查 Planner 是否根据缺失信息改变调用，而不是照抄步骤。
- 注入一次接口参数错误、一次执行失败、一次效果失败、一次验证不确定，分别检查反馈与下一步。
- 执行期间争抢同一机械臂，必须被拒绝；并发 State 查询应可运行。
- 执行完成但物体未到位时，不允许把 `completed` 当成验证成功。

故障注入属于框架验收，需与未修改环境中的策略成功率分别统计；不把注入后的任务称为官方基准成绩。

## 故事二：指定地图目标的室内导航

**用户目标：**“到地图里的 B 点；如果不知道 B 在哪里就问我，遇到走不通的路说明情况，再决定如何继续。”

采用 TurtleBot3 Burger 的小型 Gazebo 室内场景。第一轮使用预先准备的地图与可达 B 点，
任务空间为二维位置及朝向，不假定导航策略可以理解“去厨房”或自主发现未知物体。
用户自然语言理解、Memory 中的地点查找和缺失目标追问由 Planning 完成。
仿真资产使用 [TurtleBot3 仿真仓库](https://github.com/ROBOTIS-GIT/turtlebot3_simulations/tree/jazzy)。

环境必须有机器人运动模型、激光扫描、里程计、TF、地图、定位与导航 action server；
Provider 应使用仿真时钟，并明确 `map / odom / base_link` 等坐标系以及地图版本。
B 点记录至少有地图 ID/版本、frame ID、米制坐标和朝向，不能只存一对来源不明的数字。

| 域 | 能力 | 关键契约 |
| --- | --- | --- |
| State | 地图表示、当前位姿、定位质量、导航进度 | 标记时间、坐标系、地图版本；重要变化唤醒 Planner |
| Execution | 导航到指定 Pose | ROS action 映射到框架 call ID；底盘资源持续占用 |
| Verification | 到达并停止 | 使用新位姿检查位置/朝向容差和速度；附带地图版本与时间 |

Nav2 内部行为树负责一次导航的局部规划和恢复，属于 Tool 的实现。
它不应控制 Agent 的询问用户、读 Memory、切换其他 Tool 或结束整个用户任务的流程。
Agent 不能把 Nav2 action 受理当成到达，也不能把取消请求被接受当成已经停止。
Provider 必须等待终态并按控制协议确认停止，之后才释放 `base`。

缺失 B 时应询问用户；有 B 但坐标所属地图已过期时应重新确认；缺少地图时应报告缺失或发现可用建图能力。
第一轮不承诺自主探索建图：SLAM 的地图更新与探索目标选择是不同能力。
如果后续加入建图，可使用单独的建图 Provider，依然由 Planning 决定何时调用。
用户输入坐标可以先通过已有 respond 接口，图形点选另需界面能力。

建议验收样本：

- 固定场景中至少 10 组可达起终点，记录地图版本和初始位姿。
- 无 B、旧地图上的 B、阻塞的 B 三类条件；检查追问、重新观测或失败报告。
- 导航过程中持续更新 State；普通传感器帧不逐帧触发高层推理。
- 并发启动第二个底盘任务、导航取消、停止未确认、进程重启，各检查资源占用和调用关联。
- 仿真真值作为独立评测参照，在线 Verification 使用导航侧观测；定位丢失时应给 uncertain，不能用陈旧位姿报成功。

位置、朝向、停止速度阈值及观测最大年龄需在该验证工具的契约中明确；
这是具体能力的成功语义，不应写成所有 Runtime 任务的通用硬编码规则。

## 对现有架构的检查

以下结论来自当前源码静态检查和既有验收文档，不是上述仿真实验结果。

| 检查项 | 当前情况 | 故事带来的验收要求 |
| --- | --- | --- |
| 扩展与动态决策 | Registry、Skill、Provider 和 Planner 主循环已分离 | 两个能力包注册后主循环零修改；工具顺序由实际 Planner 输出 |
| 资源与生命周期 | 有原子占用、异步取消、未确认停止隔离 | 外部 SDK/ROS 也必须遵守停止契约，不能只证明 Python 协程结束 |
| 完成与验证 | Execution 与 Verification 记录分离 | `finish(success)` 目前只检查无活动调用和证据 ID 可读取，**不强制证据为成功验证、属于当前任务或仍然有效** |
| 失败预算 | 有调用/决策/活动时间/连续错误预算 | Verification failure 是正常工具结果，不必然增加连续错误计数；需测反复效果失败是否及时停止 |
| 多模态上下文 | 保存资源引用并按需读取，ContextBuilder 组织 JSON | Planner 适配器仍须把图像实际编码为模型输入；有 URI 不等于 VLM 看到了图像 |
| 地图与 GUI | 支持资源图片展示、ask_user/respond | 没有地图点选及像素到地图坐标转换；当前不能宣称已支持点击选 B |
| 重启 | 未结束调用恢复为隔离状态，不重放动作 | Provider 须持久化框架 call ID 与外部 action ID 对应关系；现有恢复偏向确认停止，不是无缝续接远端动作 |
| 外部控制权 | 资源锁仅单进程有效 | 仿真联调必须让测试 Provider 成为唯一控制入口；外部遥控/RViz 发目标会绕过该锁 |

对应源码：[任务结束与预算](../src/primitive_vla/runtime/agent.py)、
[Provider 停止契约](../src/primitive_vla/providers/base.py)、
[调用恢复](../src/primitive_vla/tools/router.py)、[上下文组装](../src/primitive_vla/planning/context.py)、
[界面资源与交互](../src/primitive_vla/interfaces/web/static/app.js)。

优先需要讨论的是“用户目标成功必须有什么证据”的契约：既不能允许错误成功，也不应强迫纯查询任务执行机器人验证。
本次只记录这一实际缺口，不擅自加通用分支或把两个故事固化成工作流。
同样，状态新鲜度、重试策略和地图坐标约束应放在相应能力契约/Skill 中，并在各自输入边界一次校验。

后续鲁棒性评测应分别报告：任务真实成功率、错误成功报告次数、效果失败后的恢复率、预算耗尽次数、
资源冲突拒绝率、停止确认耗时以及重启后的重复动作次数。拒绝已知冲突、无停止确认不释放、重启不重放
属于确定性不变量，应在全部对应样本中成立；模型任务成功率需实测，当前不设一个虚构成绩。
同时记录每次决策、Skill 版本、call ID、输入/输出、验证证据、最终状态及环境/检查点版本。
10 个状态只是首轮联调样本，不能用于宣称统计稳健或真机泛化。

## 本机条件与实施顺序

本次只读检查：x86_64、Ubuntu 22.04.5、总内存约 188 GiB、工作目录所在磁盘剩余约 80 GiB。
`nvidia-smi` 报无法连接 NVIDIA 驱动，因此 GPU 型号、可用显存和 CUDA 可用性均未确认；
这不是“本机没有 GPU”的结论，也不能据此承诺可运行 OpenVLA。

2026-11-12 更正：该失败来自受限执行权限；使用正常主机权限已确认 RTX 5090（约 32 GB）与驱动可用。
不要把此前访问失败当作硬件缺失。

1. 优先导航：无底层模型权重，先验证异步事件、资源互斥、取消和恢复。
   本机为 22.04，所选 24.04/Jazzy/Harmonic 环境需单独准备（例如容器），还需验证图形与 ROS 通信，不能直接混装。
2. 再做桌面抓取：先解决 GPU 可访问性，再按官方版本准备独立 Python 环境。
   24 GB 显存建议仍需单环境推理验证，不能把 A100 论文结果当成本机保证。
3. 两套环境建议分开、顺序运行。预计总预留约 60–90 GB，当前约 80 GiB 空间可能紧张，尤其有镜像和重复缓存时。
   实际下载前核算包管理器清单与资产大小；本次没有删除现有文件或下载大文件。
4. 若改为真机，抓取需要确认机械臂、夹爪、相机及策略动作空间是否匹配；导航需要底盘、激光雷达、里程计/TF 和停止协议。
   LIBERO 检查点不能直接视为适用于任意真机；仿真预估不包含真机标定、迁移数据或微调费用。

当前结论：框架的分层能够容纳两类任务，但实际自主性与跨系统停止协议尚待集成验证；
成功证据约束和地图交互已有明确待解决项。完成一次预设轨迹不能代替以上鲁棒性检查。
