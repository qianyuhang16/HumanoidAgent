# 面向自主具身 Agent 的评测选型

初次调研：2026-11-11；2026-11-12 补充 RPent 接入路线。本文依据原作者论文、官方仓库、任务代码和发布文件目录整理。
未运行候选环境、未复现论文成绩、未下载权重或加入具体模型/机器人 Provider。

## 选型结论

此前选择的单个 LIBERO-Spatial 抓放任务，以及已知地图上的点到点导航，保留为能力接入的冒烟测试。
它们不足以成为自主 Agent 的主验收项。这里评估的是我们选的测试范围，不是断言 LIBERO 全部任务都简单。

2026-11-12 补充：若优先考虑尽快接入真实能力，建议先选 **RPent 已适配的 RoboCasa365 复合任务**，
例如 `PrepareCoffee` / `LoadDishwasher`。它已有环境服务、动作原语、策略服务与评测清单，
比从 VLABench 的数据生成技能开始适配更直接；这仍是源码评估，尚未实测接入。
详见 [RPent 接入调研](rpent-integration.md)。VLABench 保留为后续操作候选；连续寻物的 GOAT-Bench 选型不变。

此前提出的两个主用户故事如下，任务分析继续保留：

1. **桌面多步操作：VLABench 的 `get_coffee_with_sugar`。** 涉及容器操作、按钮、倾倒和中间状态，
   用于评估技能选择、执行反馈、效果验证和局部恢复。
2. **连续寻物导航：GOAT-Bench。** 在同一房屋内连续处理类别、文字描述、图片三种目标，
   用于评估主动探索、目标辨别、Memory 复用与停止判断。

PARTNR 是额外的重要参考：它已经有 Planner、Tool、Skill、环境图以及单 Agent 基线，
很适合学习如何组织我们的集成评测。RoboCasa365 现优先通过 RPent 路线进行接入评估，
BEHAVIOR-1K 留作更广泛家庭任务的后续方向。
这些是候选与评测设计，尚不是“下载即能接入本框架”的承诺。

## 其他具身 Agent 如何评测

| 工作 | 实际评测方式 | 对我们的价值与边界 |
| --- | --- | --- |
| ReAct / ALFWorld | 在交互式家务任务中，根据动作反馈继续决策；ALFWorld 有与具身场景对应的文本环境 | 可低成本检查探索、状态记忆和失败后的动作调整；文本通过不能证明视觉或物理控制能力 |
| SayCan | 将技能序列规划与实际执行分开评估；原工作使用 101 条厨房自然语言任务 | 需要区分“选错能力”和“能力执行失败”；不能只给一个总成功率 |
| Inner Monologue | 比较成功检测、场景描述等反馈；在桌面与移动操作中观察受干扰后的重新规划 | 与 State / Verification 回流 Planning 很接近；恢复要在当前物理状态继续，不能暗中重置 |
| Embodied Agent Interface（EAI） | 单独评估目标理解、子目标分解、动作排序、状态转移建模，细分幻觉、可供性和规划错误 | 可用作决策诊断；四项评测不要求把我们的架构拆成四个独立模型 |
| EmbodiedBench | 四类环境、1,128 个测试任务，覆盖高层任务与较低层动作，按能力子集评估 | 可检查视觉信息、复杂指令与长程规划；动作粒度与观测权限必须保持一致 |
| PARTNR | 家庭任务具有空间、时间和能力约束；提供单/多 Agent、Oracle/学习式技能配置 | 既能做理想底层能力的决策诊断，也能加入真实执行难度；无需为了用它给本框架增加多 Agent 架构 |
| GOAT-Bench / NavHarness | 前者评估连续多模态目标导航；后者在此基础上研究跨会话记忆、恢复交接与完成检查 | 适合检验 Memory 是否实际改善任务完成和搜索效率，而不只是能写数据库 |
| RoboCasa365 | 区分 Atomic-Seen、Composite-Seen、Composite-Unseen，评估复合任务与组合泛化 | 可避免只在熟悉脚本中成功；更换底层策略后不能把全部增益归因于 Agent |

来源：[ReAct](https://react-lm.github.io/)、[ALFWorld](https://github.com/alfworld/alfworld)、
[SayCan](https://say-can.github.io/)、[Inner Monologue](https://innermonologue.github.io/)、
[EAI](https://embodied-agent-interface.github.io/)、[EmbodiedBench](https://github.com/EmbodiedBench/EmbodiedBench)、
[PARTNR](https://github.com/facebookresearch/partnr-planner)、
[GOAT-Bench](https://mukulkhanna.github.io/goat-bench/)、[NavHarness 论文](https://arxiv.org/abs/2609.34276)、
[RoboCasa365 评测协议](https://robocasa.ai/docs/build/html/benchmarking/multitask_learning.html)。

值得借鉴的实验是**固定模型与底层能力，改变框架组件**。例如比较有/无跨任务记忆、
有/无验证反馈、有/无恢复交接；同时约束调用、动作、时间和 token 预算。
NavHarness 还使用等长度摘要作交接对照，避免把“给了更多上下文”误判为记忆结构更有效。
这些是作者的实验设计，不是本项目已经测出的收益。

不能直接拿榜单上的百分比作公平比较。例如 RoboCasa 的 HexaShell 1.5 提交明确说明：
使用自定义 pretrain50 划分、每任务 30 次评测，结果由作者运行且待官方验证；
每个任务已有技能笔记，因此 Composite-Unseen 对该系统不属于零样本。
应先检查划分、观测权限、记忆来源、控制方式和预算，再判断成绩意味着什么。
来源：[该提交的协议说明](https://github.com/robocasa-benchmark/leaderboard/blob/main/submissions_md/HexaShell-1.5_2026_10_31.md)。

## 用户故事 A：桌面制备加糖咖啡

**用户目标：**“帮我准备一杯加糖咖啡。”

VLABench 的官方实现包含 `get_coffee`、`get_coffee_with_sugar` 等任务。
加糖版本涉及杯子位置、机器激活和倾倒条件，动作能力超出单次抓放。
它的官方代码带有专家技能序列，可用于环境连通性和动作能力自检，
**不能调用整条专家序列后，把成功归因于我们的自主规划**。
来源：[咖啡系列任务代码](https://github.com/OpenMOSS/VLABench/blob/main/VLABench/tasks/hierarchical_tasks/composite/get_coffee_series.py)。

接入时可暴露观测、抓取、移动/放置、按钮操作、倾倒等独立能力，由 Agent 选择和组合；
机器人坐标转换及 IK/轨迹跟踪属于底层实现。对于 SkillLib 中依赖精确物体关键点的动作，
要明确标为使用仿真真值的诊断轨道，不能宣传为纯视觉操作。
官方 SkillLib 原本用于数据生成，其取消、进度和框架 Tool 契约仍需适配验证。
来源：[SkillLib](https://github.com/OpenMOSS/VLABench/blob/main/VLABench/utils/skill_lib.py)。

在该故事中，拟重点观察：

- 杯子位置或已完成状态变化后，Agent 是否先识别当前进度，再选择必要动作。
- 某次抓取/按压未产生效果后，是否读取新证据并调整，而不是继续后续步骤。
- 返回 uncertain 时是否补充观测，而不是把“没看清”改写为成功。
- 相同目标、不同初始条件下，是否都满足最终目标；不要求每条正确轨迹都具有相同顺序。
- 执行过程中暂停/恢复或遇到预算限制时，是否保留真实状态与控制占用。

上述扰动与中途状态变体属于**本项目的鲁棒性扩展集**，应和未修改的官方任务分开统计。
Agent 不接收专家行动序列或未来状态；评测器保存的真值不得经 Memory 自动泄漏到视觉轨道的上下文。

**成功判据需要审查。** 该任务当前使用机器激活、容器关系和倾倒等仿真条件。
这些条件不等于已经验证真实液体体积、温度、口味或食品制备过程。
我们应分别报告官方谓词是否满足、Agent 如何宣告成功，以及额外用户目标是否得到证据支持。
若补充更严格的判据，须标明自定义协议，不能继续把它叫作原版成绩。

VLABench 还包含钉钉子/挂画、整理物品等复合任务，但第一轮不宜只按名称选择最复杂的一项。
先确认可运行的 episode、动作覆盖和成功条件，才能避免把环境缺陷当成 Agent 错误。
其公开微调数据和部分策略检查点以 primitive 任务为主，不能假定已经有能稳定完成咖啡任务的现成 VLA。
来源：[任务目录](https://github.com/OpenMOSS/VLABench/tree/main/VLABench/tasks/hierarchical_tasks/composite)、
[官方评测与检查点范围](https://github.com/OpenMOSS/VLABench#evaluate)。

## 用户故事 B：同一房屋中的连续寻物导航

**用户目标示例：**“找到一把躺椅；然后找到照片中的烤箱；接着去客厅茶几上的白色书旁边。”

这是 GOAT-Bench 任务形式的示例，不是假设这三个对象一定存在于我们任意选的场景。
正式运行应使用数据集的有效 episode。每个 episode 有 5–10 个依次给出的目标，
包括类别、实例图片和语言描述；子任务之间保留当前环境及位置，从而考察历史探索是否能复用。
原协议报告子任务成功率和 SPL（成功率加权的路径效率）。
来源：[原论文任务与指标定义](https://arxiv.org/html/2404.06609v1)。

相较“给定坐标，到 B 点”，这里增加了：

- **不知道目标在哪里：** Agent 必须选择搜索区域，而不是直接向导航器传入答案坐标。
- **相似目标需要辨别：** 到达某个同类物体不一定满足图片或语言指定的实例目标。
- **需要跨目标记忆：** 前一次经过但尚未被要求寻找的物体，可以在后续任务中成为目标。
- **需要正确停止：** 找不到、路径超预算与到达目标应有不同结论。

建议用同一批 episode 比较完整框架和关闭跨目标 Memory 的版本，保留相同当前观测与模型预算。
同时报告每个目标的 SR/SPL、整条目标链全部完成率，以及随目标序号变化的搜索效率。
整链完成率是我们增加的指标，不能误称为原论文默认报告项。

原版 GOAT 使用 RGB-D 及相对 GPS/Compass 位姿观测，不能将其结果解释为已解决真实 SLAM。
若换成 SLAM 估计位姿，应单列实验，并记录定位失败与地图修订。
NavHarness 提供这一路线的参考，涉及独立 Habitat 进程、ORB-SLAM3、记忆检索和完成检查；
它的实现和额外评测条件不能直接等同于原版 GOAT 协议。
来源：[GOAT 官方实现](https://github.com/Ram81/goat-bench)、
[NavHarness 实现与配置](https://github.com/billzhao1030/NavHarness)。

GOAT 基础场景是扫描环境，本故事不自带开门、抓取、真实动态行人或用户澄清协议。
若进一步要求“找到杯子并搬到厨房”，优先考虑 PARTNR 或 RoboCasa，而不是给静态寻物基准虚构操作能力。
旧地图、传感器断流、人工改目标等也属于另外的鲁棒性扩展，不能混入原版榜单数据。

## 环境、权重与落地成本

以下文件大小来自本次查到的官方目录，使用十进制 GB/MB；它们不是运行显存，也不是总安装大小。
高层 Planner 若使用远程服务，本地无需其权重；若本地运行，需要另外核算。当前不选择或安装具体模型。

| 方案 | 环境事实 | 可确认的权重/资产情况 | 对本项目的影响 |
| --- | --- | --- | --- |
| VLABench | 官方环境 Python 3.10；MuJoCo 3.2.2、dm_control 1.0.22 | IK/技能原语诊断不要求神经动作权重；官方 `pi0-primitive-10task` 的 params 目录约 **12 GB**，整个仓库 **43.1 GB**（含 train_state） | 该检查点不是咖啡任务完成保证；仍需验证原语覆盖、输入权限和可中断执行。场景/物体资产精确大小尚未核实 |
| GOAT-Bench 原版 | 官方安装 Python 3.7、Habitat 0.2.3；HM3D 场景与 GOAT episodes | monolithic 检查点约 **77.8 MB**；全部发布 checkpoints 约 **153 MB**；含目标特征缓存的仓库共 **4.72 GB** | 这些数字不含全部视觉编码器、场景与环境；基线策略也不是我们的高层 Agent。旧 Python 与本项目 3.10+ 不兼容，需要隔离环境或受测的迁移 |
| GOAT / NavHarness 参考路线 | Linux/NVIDIA EGL；Runner Python 3.10、Habitat 0.3.3/Python 3.11、CPU ORB-SLAM3 | 该项目估计 HM3D 验证场景约 **5 GB**；GOAT-only 不需要它用于另一基准的 MP3D 场景 | 不是 GOAT 全量资产大小；需数据访问许可、环境构建和渲染验证，不能把它当成总磁盘需求 |
| PARTNR | 官方 Python 3.9.2、Habitat 0.3.3、PyTorch 2.4.1 | Oracle 基线不要求动作网络；全部发布技能检查点约 **172 MB**；HSSD partnr 分支约 **10.7 GB** | 另需机器人、OVMM、episodes 等资产；已有单 Agent 配置，可做带特权底层技能的规划对照；Python 版本需要隔离 |
| RoboCasa365 | 官方 Python 3.11、robosuite/MuJoCo | 官方安装文档提示厨房资产约 **10 GB**；策略权重另选 | 300 个 composite 任务，适合后续扩大操作范围；仿真器本身不提供一个解决全部任务的通用策略 |

容量来源：[VLABench params](https://huggingface.co/VLABench/pi0-primitive-10task/tree/main/params)、
[VLABench 检查点仓库](https://huggingface.co/VLABench/pi0-primitive-10task/tree/main)、
[VLABench 依赖](https://github.com/OpenMOSS/VLABench/blob/main/requirements.txt)、
[GOAT 单策略](https://huggingface.co/datasets/axel81/goat-bench/tree/main/checkpoints/sense_act_nn_monolithic)、
[GOAT checkpoints](https://huggingface.co/datasets/axel81/goat-bench/tree/main/checkpoints)、
[GOAT 缓存仓库](https://huggingface.co/datasets/axel81/goat-bench/tree/main)、
[PARTNR 安装](https://github.com/facebookresearch/partnr-planner/blob/main/INSTALLATION.md)、
[PARTNR 技能权重](https://huggingface.co/datasets/ai-habitat/partnr_episodes/tree/main/checkpoints)、
[HSSD partnr](https://huggingface.co/datasets/hssd/hssd-hab/tree/partnr)、
[RoboCasa 安装](https://robocasa.ai/docs/build/html/introduction/installation.html)。

更重的备选是 BEHAVIOR-1K / OmniGibson，支持更丰富的家庭活动及物体状态。
官方当前最低要求列出 Ubuntu 22.04+/Windows 10+、32 GB RAM、RTX 2070+、8 GB VRAM。
这是模拟器门槛，不包含本地高层模型和控制策略；不能据此保证整套系统在 8 GB 显卡可运行。
来源：[BEHAVIOR 安装](https://behavior.stanford.edu/getting_started/installation.html)、
[能力范围](https://behavior.stanford.edu/other/faq.html)。

本机上轮只读检查得到约 80 GiB 可用磁盘，受限执行环境中 NVIDIA 驱动不可访问；
2026-11-12 已用正常主机权限确认 RTX 5090 可用，不能将此前沙箱访问失败解释为主机没有 GPU。
不应同时下载全部基准、训练状态和训练数据。下一步先确认单环境渲染与一个 episode，
再锁定实际需要的依赖/资产清单。公开包体大小不构成当前机器已经具备运行条件的证据。

## 我们应采用的评测设计

**先分清三个层次，再逐层归因：**

| 层次 | 固定什么 | 评什么 |
| --- | --- | --- |
| 决策诊断 | 固定可供调用的原语与观测权限；必要时使用明确标记的真值/Oracle 底层 | 能否补全信息、选择工具、满足前置条件、解释失败并调整决策 |
| 视觉与执行闭环 | 换为真实渲染输入、实际控制器/策略，保留同样的目标与调用契约 | 感知错误、抓取/导航失败、验证误判如何传递并恢复 |
| Runtime 鲁棒性 | 固定 Planner 和执行器，注入可复现的异常 | 控制互斥、停止确认、资源释放、会话恢复、证据一致性与预算 |

Oracle 不能预先给出完整任务解法，也不能代替待测的 Planner。
同步模拟器中的成功不证明异步机器人取消协议正确，第三层必须单独检查。

首轮选择少量固定 episode 作为联调样本；正式报告使用预先声明的划分、种子和预算，
保留所有失败样本并报告样本数。初始条件、观测输入、历史记忆和底层技能应保持可比较。
尤其要区分：先在相同场景训练/调试后评测，和真正未见场景/未见任务组合。

建议对照完整框架、仅当前上下文、不向规划器提供验证反馈、无跨任务 Memory、无恢复交接几个配置。
这些是实验条件，不能在正常运行时撤掉 Execution 的控制互斥或停止确认机制。
日志至少记录最终目标完成率、子目标进度、错误成功报告、失败后恢复率、工具/动作/token 开销及墙钟时间；
导航另记路径效率和整链成功。禁止通过 episode 重置或增加重试预算制造恢复收益。

2026-11-12 VLM 重构已补齐按需图像输入与统一模型接口，见 [VLM 接入](deepseek.md)。
成功证据约束、地图版本/坐标和跨系统停止协议仍需在具体能力集成时检查，
原始分析见 [用户故事的静态检查](user-stories.md#对现有架构的检查)。
本轮只调整评测选型，没有把咖啡或导航故事写进 Runtime，没有新增模型工具，也没有宣称鲁棒性已验收。
