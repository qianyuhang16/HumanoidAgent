# RoboCasa 平台包

这个目录是一份独立平台接入，不属于 Agent 核心。模型选择与平台代码分离。

- `platform.json`：平台身份、内部 Python 依赖路径、环境变量及默认设置。
- `profiles/`：开抽屉、做咖啡、厨房导航、台面抓取和双食材入锅的场景参数与 Skill 文件选择。
- `skills/<域>/<工具>/skill.md`：每个工具一个文件，声明名称、Schema、资源、超时、实现入口及使用知识。
- `runtime/server.py`：自行读取本包的 platform.json、profiles/ 和 skills/，初始化环境后向宿主报告工具、Skill 与相机。
- `runtime/environment.py`：机器人能力适配、停止确认与 episode 生命周期。
- `runtime/backend.py`、`views.py`：原生模拟器线程与相机订阅。
- `requirements.txt`：模拟器依赖，RPent 源码另放 `deps/RPent`。

平台包可以放在任意位置；内部路径相对于本包根目录。`assets/`、`deps/`、`runs/` 为本机内容，Git 忽略。
模型配置单独放在平台包之外。宿主启动配置通过 command/args/cwd 选择解释器和本包入口，并传入 profile、run_dir 和 options。
启动示例见 configs/runtime.robocasa.example.json；解释器环境中需安装框架包和仿真依赖。
完整安装见 [RoboCasa 文档](../../docs/robocasa.md)，装配与启动见 [工作区装配](../../docs/assembly.md)。

开抽屉与咖啡配置装配全部 12 个工具文件，导航配置装配 6 个；没有 Python 工具注册清单。
可从 [observe/skill.md](skills/state/observe/skill.md) 查看一个完整例子。
修改或新增文档并重新启动即可改变注册结果，新算法需在 entrypoint 对应的模块实现。
这些文件提供工具契约和使用知识，不是专家轨迹或经过成功率评测的策略。
RoboCasa 进程重启不恢复物理状态。旧运行目录重启需要显式 new_episode，且不会复用旧任务继续动作。

## 台面抓取 Demo

在已安装仿真依赖和资产、已配置 `configs/vlm.json` 的机器上，从仓库根目录启动：

```bash
bash scripts/start-desktop.sh configs/vlm.json configs/runtime.robocasa-tabletop.example.json
```

`tabletop-grasp` 使用 `runtime/tasks.py` 中的本地 RoboCasa `TabletopGrasp` 场景和现有 12 个工具。
机器人初始面向台面上的单个柠檬，任务是在夹持状态下抬升至少 8 cm。
成功谓词同时检查双指接触与相对初始高度；该自定义场景不是 RoboCasa 官方基准成绩。
在客户端输入“抓起台面上的柠檬，抬高至少 8 厘米并保持夹持，验证是否成功”。
工具调用顺序、像素选择与失败恢复由模型决定，环境定义中没有控制动作序列。
该配置显式使用 `new_episode: true`，每次重新启动创建新的物理场景，旧任务只保留历史，不恢复动作。
记录保存在 `runs/tabletop-grasp/`。启动配置和 Skill 包本身不代表任务已经验证成功。

## 双食材入锅

```bash
bash scripts/start-desktop.sh configs/vlm.json configs/runtime.robocasa-two-vegetables.example.json
```

`two-vegetables-in-pan` 使用上游原生 `PlaceVegetablesEvenly` 场景，固定 seed 为 1，
装配现有 12 个工具。两种蔬菜初始在灶台旁的台面上，平底锅初始在灶台上；
食材种类由场景生成，Agent 通过观察确认。
在客户端输入“把台面上的两种蔬菜都放进平底锅，分开放置，不要堆叠，完成后验证”。

`verification.robocasa.task` 使用上游成功判据：两种蔬菜均在锅内，中心高度差小于 2 cm、
水平距离大于 6 cm，且夹爪远离两者。此任务不要求点火或烹饪。
配置只选择环境和工具，不规定抓取顺序、路径、像素或恢复动作。
每次启动创建新 episode；运行记录保存在 `runs/two-vegetables-in-pan/`。
单次运行结果不能视为官方基准成功率。

工具输出按用途收敛：相机返回图片及相机/尺寸信息。像素定位只传图片引用、row、column；
episode_id、simulation_steps 留在资源私有元数据中，由平台读取并检查过期帧。
位姿通过 observe 主动查询。动作只返回原语结果，进度只返回步号，验证返回任务进展、依据和独立结论。
任务配置、种子和完整状态不再随每次成功动作重复返回；失败证据仍保留原始信息。
