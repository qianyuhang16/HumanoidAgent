# 通用能力 Agent：电池与盖积木共用三个接口

本入口不加载任务专用 `Registry`、符号候选动作或任务成功谓词。
三份通用 `SKILL.md` 作为模型的能力说明读取，不实例化电池或盖积木的符号技能。
Agent 只接收用户目标、当前 RGB、可查询的 RGB-D 几何、机器人关节反馈和控制预算。
模型仍为 Codex gpt-5.5 medium，感知仍为官方 Grounding DINO + SAM，动作仍为官方
Mem-0 对应任务的 executor。没有训练新权重，也没有把 executor 当作通用机器人策略。

## 三份跨任务共用的能力说明

- [Grounding DINO + SAM](../../../src/primitive_vla/plugins/generic_tools/grounding_sam/SKILL.md)：`perceive(query)`。
- [VLA](../../../src/primitive_vla/plugins/generic_tools/vla/SKILL.md)：`execute(instruction, max_actions)`。
- [IK](../../../src/primitive_vla/plugins/generic_tools/ik/SKILL.md)：`move_to(targets)`。

`observe`、`finish`、`stop` 是回合协议操作，不是新增物理能力。三份文件只说明输入输出和
能力边界，不包含任务实体、操作顺序或模板指令。Python 负责结构化参数和调用实现。

## 结构与能力边界

- `src/primitive_vla/agent/tool_loop.py`：通用模型—工具循环。传回模型自己的工作记忆与调用结果；
  不执行电池组合枚举，也不把记忆转成物理事实。
- `src/primitive_vla/backends/rmbench_tools.py`：通用工具接口、检测句柄有效期、VLA 控制租约与停止确认。
- `src/primitive_vla/plugins/grounded_sam/model.py`：接收每次调用的查询词；检测、分割、深度反投影。
- `src/primitive_vla/simulation/rmbench_sensors.py`：传感器与控制适配，仅结束后允许独立评分。
- `episode.py`：实验生命周期及报告。Agent 终止报告落盘后才调用 native evaluator。
- `planner.md`：通用证据、记忆与预算提示，无电池操作方案。

| 工具 | 模型自由决定的内容 | 程序固定的边界 |
| --- | --- | --- |
| perceive | 检测查询词、何时调用 | DINO/SAM 权重、阈值、RGB-D 反投影 |
| move_to | 检测 ID、左/右机械臂、垂直间距 | 标定工具方向与偏置、IK 路径规划；不抓取 |
| execute | 英文子任务指令、本次动作预算 | Mem-0 动作预测、结束分类器、总动作预算与停止确认 |
| observe | 何时再次读取图像 | 不移动、不检测；同一静止状态不会产生新视角 |
| finish | 完成判断与证据 | 直接记录声明，不再用预设视觉谓词限制 |
| stop | 放弃执行的理由 | 终止回合 |

所有运动都会使旧检测 ID 失效。只有新鲜且有有效深度与合格掩码的检测可供 IK 使用。
这属于坐标与控制有效性约束，并不判断任务是否完成。模型可以不调用 IK，直接执行 VLA。
系统不提供左右电池角色绑定、正负方向菜单、双正安装模板或仪表激活判据；这些必须由模型
从用户目标、图像和通用能力说明中自行提出。`execute` 原样传递模型的指令，不替换成官方模板。

执行每次返回新鲜 RGB，但不强制运行 DINO/SAM，也不自动插入特定恢复操作。
每次 execute 会重置 VLA 时序记忆，并在结束分类器触发或所选预算用完后交还控制。
因此过短预算、频繁重新下发指令可能影响底层动作连续性。当前没有跨调用续接策略状态的工具。

## 实验协议

```bash
PYTHONPATH=src .venv/bin/python benchmarks/mem0/run.py \
  --config benchmarks/mem0/generic_tools/config.json \
  --output workspace/mem0/battery-generic/test-v0 \
  --seeds 300101 300102 --modes vla --workers 1
```

这里命令行 `--modes vla` 只表示关闭 worker 的自动 IK。Agent 仍可显式调用 move_to。
300101 是已知场景对照，300102 是本轮新种子；应分别报告，不能把二者混为泛化成功率。
最多 32 次工具决策，每次最多 450 个 VLA 动作，环境沿用 1000 动作上限。
未知任务极性、物体真值位姿、阶段、奖励和成功信号均不进入 Agent 或控制接口。

相比任务专用技能版本，不仅动作菜单改变，完成声明门槛、感知时机、IK 调度和 VLA 时序
上下文也改变了；这轮用于验证通用工具链和发现失败模式，不是单一变量的因果消融。
原技能和旧配置保留，没有把之前成功轨迹作为模型输入。

[回放、自由生成的模型查询与子任务](http://127.0.0.1:8085/battery-generic/test-v0/)

## 验证

自动测试共 67 项：64 通过、3 跳过。新测试覆盖自由查询与不受任务谓词限制的完成声明、
过期检测拒绝、VLA 指令原样传递与动作预算、停止未获确认后锁定控制，以及无任务菜单的 schema。
实际仿真结果如下。


## 同接口切换至 cover_blocks

使用 `cover.json`，共用完全相同的三个能力说明、JSON 参数接口和 `planner.md`。
配置差异只有用户目标、场景名称、executor checkpoint 和动作归一化统计。
用户目标规定先按左中右盖住，再按红绿蓝揭开；这是 benchmark 的顺序要求，
并未告诉 Agent 每一步的目标检测 ID、颜色位置、机械臂、子任务文本或恢复办法。

```bash
PYTHONPATH=src .venv/bin/python benchmarks/mem0/run.py \
  --config benchmarks/mem0/generic_tools/cover.json \
  --output workspace/mem0/cover-generic/test-v0 \
  --seeds 300102 --modes vla --workers 1
```

顺序任务需要环境内部保留轨迹验收状态，否则终态无法区分正确顺序与错误顺序。
该状态只在模拟器内部更新，不导出阶段、奖励、fail_flag 或成功信号，也不触发提前停机。
最终布尔评分仍在 Agent 终止报告落盘后取得。这里的内部轨迹记账不是 Agent 的状态记忆。

[盖积木回放](http://127.0.0.1:8085/cover-generic/test-v0/)


## 实测结果与失败边界

| 任务 / 种子 | 渲染 | Agent 调用 | 感知 / IK 调用 | 已确认 VLA 动作 | Agent 声明 | 独立终态评分 |
| --- | --- | --- | --- | --- | --- | --- |
| battery_try / 300101 | 光追 | 6 | 2 / 1 | 310 | 未完成 | 不可用：相机读回卡死 |
| battery_try / 300102 | 光追 | 11 | 2 / 2 | 1000 | 未完成 | 失败 |
| cover_blocks / 300102 | 光追 | 9 | 2 / 1 | 1467 | 未完成 | 不可用：相机读回卡死 |
| cover_blocks / 300102，补跑 | 普通渲染 | 3 | 0 / 0 | 334 | **完成** | **失败：一次错误完成声明** |

两个获得有效终态评分的回合均未成功，另有两次基础设施中断。
这些样本和渲染条件不足以估计跨任务成功率，也不能与之前任务专用技能版的两例成功
直接作因果比较。盖积木环境预算为 1500 动作，电池为 1000；两者每次 execute 均最多 450。

### 电池：自由调用成立，物理任务失败

模型自行查询电池与槽位，选择双臂 IK，随后自由生成插入指令。它还引入了未获图像证实的
“按下/拨动电源开关”，并把移开机械臂、检查、条件补插、开机打包进 VLA 指令。
新种子最终仍有电池在槽外，1000 动作耗尽；Agent 正确停止，没有声称成功。
自由指令与控制结果不一致，但本轮不能区分长指令、IK 入口、任务训练分布等各因素的贡献。

### 盖积木：对象角色混淆与过程证据缺失

光追尝试中，Agent 最初把全任务交给 VLA；后来自主改用局部指令、DINO/SAM 和 IK。
它将彩色块称为盖子，使用 blue/red/green cover 查询，检测器也返回相应标签，
形成了查询假设与检测结果相互强化的现象。检测框命中并没有独立证实对象的功能角色。

普通渲染补跑使用 `cover_raster.json`，仅将 cover.json 的 camera_shader 改为 default。
模型执行一次长指令（334 动作，learned_end=true），observe 后直接 finish。
完成理由把“已下发完整顺序指令”当成“顺序已完成”，又用三个块最终露出佐证成功。
但相同可见终态也可以是初始状态；顺序约束需要过程证据。终态原生评测返回 false。
此次没有使用感知或 IK，是 Agent 的自主选择，不能算作三种能力都成功配合的案例。

**结论：统一的三个通用能力入口已经跨任务接通，但当前通用 Agent 尚未可靠解决任务。**
没有恢复任务专用动作菜单、固定方向序列或预设视觉成功门槛来隐藏这些失败。
下一步应聚焦任务无关的中间层：由模型提出可观察的子目标、记录其证据、区分下发与达成，
并在定位前核对对象角色，而不是为每个失败再增加一份任务技能。

### 中断与复现边界

电池中断时 worker 打印到 323 步，最后完整返回 310 步；盖积木中断打印到 1471 步，
最后完整返回 1467 步。SIGUSR1 栈均停在 CUDA 相机 Color 读回，操作者仅终止了本次
卡死的 world worker。无法获得停止确认，控制保持锁定，未伪造终态评分。
普通渲染补跑完成一次评测，只说明此回合没有卡死，不代表光追/驱动问题已经修复。

电池初次实验的三个能力说明先以字符串提供，随后原文提取到共用 SKILL.md；说明文本不变。
后续增加的 cover_blocks 环境记账适配未向 Agent 暴露评分状态。各次运行自身的代码与提示
哈希保持冻结，既有四份任务技能未修改。

- [电池审计](http://127.0.0.1:8085/battery-generic/test-v0/audit.json)
- [盖积木光追审计](http://127.0.0.1:8085/cover-generic/test-v0/audit.json)
- [盖积木补跑审计](http://127.0.0.1:8085/cover-generic/raster-retry/audit.json)

审计未发现 Agent 日志中的任务真值字段；IK 目标均源于感知测量；下发 VLA 的指令与模型
原始输出一致；Agent 终止报告不包含后续终态评分。日志检查是数据边界核对，不是形式化证明。


## 提示词修订：逐步规划（stepwise-v1）

本轮只修改 [planner.md](planner.md)，没有添加 Skill、执行拦截规则或任务专用程序。
三个能力说明、模型权重、代码、种子 300102、普通渲染和所有配置与 raster-retry 相同。
旧、新提示原文分别保存在对应运行目录的 planner.md 中；上方初版结果使用旧提示。
当前配置路径默认读取新版提示，复现旧版时需使用其归档提示。

新版要求模型：

1. 从目标生成有依赖关系的子目标，每项带可观察完成条件与 pending/active/verified/uncertain 状态。
2. 在工作记忆中保留当前一步和历史证据；只把当前一个局部目标交给 VLA。
3. 不把后续阶段、条件判断、检查或整段计划塞入 execute。
4. 根据动作后观测更新状态；结束分类器和预算耗尽均不等于子目标达成。
5. 对有序或可逆任务，完成声明须有中间转移证据，不能只看最终外观。

### 同种子复测

| 指标 | 旧提示 raster-retry | 新提示 stepwise-v1 |
| --- | --- | --- |
| Agent 调用 | 3 | 11 |
| VLA 调用 | 1，包含全部盖住/揭开步骤 | 5，均为单个局部目标 |
| 感知调用 / IK 调用 | 0 / 0 | 5 / 0 |
| VLA 动作 | 334 | 747 |
| Agent 最终判断 | 声明完成 | 主动停止，不声明完成 |
| 独立评分 | 失败 | 失败 |

模型自行生成了六个子目标；第一条指令为
`Place the blue cover onto the left wooden block so the block is covered.`
该指令的粒度符合单步要求，**但对象角色仍然错误**：模型继续把彩色块当作盖子。
第一条指令只执行 7 个动作便触发 learned_end，Agent 没有将其标为完成，而是感知复查。
之后进行了三次同一放置关系的重试和一次单独的清臂恢复，再次感知后主动停止。
最终第一步仍未被验证，后续步骤保持 pending，没有整段派发或错误完成声明。

停止时仍剩 753 个动作预算；“不够可靠完成后续步骤”是模型的判断，不是程序强制预算终止。
耗时 498.38 秒。本次没有相机中断。

**本例显示指令粒度和完成判断有所改善，不代表任务已解决或控制效率提高。**
提示要求的中性查询、对象角色复核没有被充分执行，重复局部尝试仍存在；本轮也未验证
正确完成多个子目标后的连续推进能力。单次配对回放不能证明统计可靠性或跨任务泛化。

[新旧回放对照](http://127.0.0.1:8085/cover-generic/stepwise-v1/) ·
[提示与指令对照记录](http://127.0.0.1:8085/cover-generic/stepwise-v1/prompt-comparison.json) ·
[数据边界审计](http://127.0.0.1:8085/cover-generic/stepwise-v1/audit.json)
