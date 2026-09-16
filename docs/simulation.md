# SAPIEN / RoboTwin 仿真接入

当前支持 **Franka Panda 单臂夹爪**：Agent 依次抓取三个配料色块放入碗中，抓起搅拌棒，在碗中搅拌，最后把工具放回工具区。每次执行后重新读取物理状态，再决定下一步技能。

> 当前 `configs/sapien.json` 使用 MOVE_TO + π0.5 base。下文脚本操作与九步成功记录属于历史基线；当前 VLA 抓取尝试未成功，实测与控制边界见 [VLA 控制](vla-control.md)。

## 启动

本机已经具备：

- RoboTwin：`/home/estar/czx/RoboTwin`
- 仿真 Python：`/home/estar/miniconda3/envs/robotwin/bin/python`
- SAPIEN `3.0.0b1`、NumPy `1.26.4`、SciPy `1.10.1`、Pillow、ImageIO 与 FFmpeg。
- NVIDIA RTX 5090；使用 Vulkan 离屏渲染，无需桌面窗口。

```bash
# 项目环境启动网页；它会按配置自动启动独立的仿真进程
.venv/bin/python -m primitive_vla serve \
  --config configs/sapien.json --workspace workspace/sapien
```

打开 **http://127.0.0.1:8081**，点击“开始执行”。默认指令：

> 把所有配料放入碗里，然后搅拌并放回工具

网页展示实际相机帧、目标、执行时间线和事实来源。完成后点击“查看任务录像”。历史任务也可通过 `/?task=<任务 ID>` 直接打开。

CLI 独立运行：

```bash
.venv/bin/python -m primitive_vla run \
  --config configs/sapien.json --workspace workspace/sapien-cli \
  --task examples/collect_and_stir.json
```

同一工作区由一个服务持有；网页运行期间通过网页提交任务。新的任务会创建新的仿真世界，并使用配置中的物体初始位置。

其他机器上修改配置中的 `python_executable` 和 `robotwin_root`。如果没有现成仿真环境，可在专门的 Python 3.10 环境安装 `pip install -e '.[simulation]'`；还需按 [RoboTwin 安装说明](https://robotwin-platform.github.io/doc/usage/robotwin-install.html) 准备机器人资产及兼容的图形驱动。

## 接入层次与代码目录

```text
网页 / CLI → TaskService → Runtime → Agent / Planner
                                      ↓ SkillCall
                              backends/sapien.py
                                      ↓ JSON Lines
                         robotwin 环境中的 SAPIEN 进程
                            ├── simulation/worker.py
                            ├── simulation/scene.py
                            └── simulation/motion.py
```

| 文件 | 职责 |
|---|---|
| `backends/sapien.py` | 异步进程适配、观测、技能请求、阶段反馈、停止确认与关闭 |
| `simulation/worker.py` | 进程协议与仿真时钟；全部 SAPIEN 调用在同一线程执行 |
| `simulation/scene.py` | 场景、URDF、物体、相机、接触与位置观测、录像 |
| `simulation/motion.py` | 从 SKILL.md 读取动作程序，提供笛卡尔轨迹、逆运动学与关节驱动算子 |
| `config/schema.py` | `SimulationConfig` 与配置边界 |
| `configs/sapien.json` | 本机可运行的完整仿真配置与技能契约 |
| `examples/collect_and_stir.json` | 各物体入碗、搅拌、工具归位与夹爪空闲目标 |

复用 [RoboTwin](https://github.com/RoboTwin-Platform/RoboTwin) 的 `assets/embodiments/franka-panda/panda.urdf` 及其网格，在 SAPIEN 中构建本项目的厨房场景。运行时使用 SAPIEN 内置 Pinocchio 逆运动学与关节驱动；RoboTwin 的 `Base_Task`、CuRobo 任务脚本和评测器不参与此任务执行。

当前配置使用 Codex GPT-5.5 / medium 解析目标、逐步选择技能并审阅结果，复用本机 Codex 登录，详见 [Codex 接入](codex-integration.md)。模型输入包含当前仿真图像与技能 Markdown；等待模型时每 0.5 秒更新观测。也可在网页切换 HTTP provider 接入 [本地 Qwen](local-vlm.md)，或选择模板解析与规则规划。低层动作由执行器与物理后端完成。

## 配置

主要字段位于 `backends.sapien.simulation`。网页“物理仿真配置”提供常用参数，完整 JSON 可以编辑其余字段。

| 配置 | 作用 |
|---|---|
| `python_executable`, `robotwin_root` | 独立环境与机器人资产位置 |
| `objects` | 配料 ID、桌面 XY 位置、RGBA 颜色；数量由列表决定 |
| `object_size` | 配料立方体边长，单位米，需适合夹爪开口 |
| `bowl_position`, `bowl_radius`, `bowl_height` | 固定容器位置及尺寸 |
| `tool_position`, `tool_home_radius` | 工具初始位置与归位区域；归位允许工具平放 |
| `physics_hz`, `control_hz`, `realtime_factor` | 物理频率、控制频率、目标运行倍率；实际速度受渲染负载影响 |
| `arm_*`, `gripper_*` | 关节刚度、阻尼、力上限 |
| `static_friction`, `dynamic_friction` | 物体与夹爪接触材质 |
| `tcp_offset`, `speed_m_s`, `travel_height` | Panda 工具中心偏移、转运速度与抬升高度 |
| `stir_turns`, `stir_radius`, `stir_depth`, `stir_period_s` | 搅拌圈数、半径、深度和轨迹段时间；深度相对碗底基准 |
| `min_stir_contact_steps` | 搅拌棒接触配料的最少物理步数 |
| `camera_position`, `camera_target`, `width`, `height`, `render_fps` | 相机视角、尺寸与记录帧率 |
| `record_video` | 是否保存 MP4；相机快照仍用于网页和模型输入 |
| `startup_timeout_s` | 仿真进程启动上限 |

技能文件位于 `src/primitive_vla/skills/sapien/*/SKILL.md`，说明与动作顺序都可直接编辑。修改物体数量时，同步更新 `scene.objects`、`scene.facts` 和 STIR Markdown 契约中各物体的 `at:<ID>:{target}` 前置条件。自然语言模板会根据 `ingredient` 对象自动生成入碗目标；使用任务 JSON 时也相应调整目标列表。场景与符号对象 ID 保持一致，规划器才能预测“所有物体先入碗”这一依赖。

技能参数与超时、重试、提示词、全任务预算仍使用原有配置分组。`stir_period_s` 定义轨迹段时间，驱动收敛与接触检查会增加实际单圈时长。示例通常产生九个技能调用，但动作顺序由规划器依据实时状态确定。

## 物理证据与成功条件

- **抓取**：闭合后检测物体与两侧手指的接触，抬起后再次确认夹持。物体由碰撞、摩擦和关节驱动运动。
- **入碗**：夹爪释放后，依据物体中心的径向与高度范围确认其位于容器内部。
- **搅拌**：配置的全部配料先入碗；累计实际工具尖端在碗内的角位移，同时记录工具与配料的接触步数。技能 Markdown 中的 `extra_turns: 2` 允许最多额外两圈轨迹以补偿接触滑动；证据不足则报告失败。
- **归位**：工具已释放、位于工具区内且线速度低于阈值，允许侧放。
- **取消**：停止当前轨迹生成，保持当前手臂目标与夹爪驱动力，再返回停止确认。后续任务重新创建场景。

每帧观测包括关节位置、物体位置、夹持接触、仿真时间、搅拌角位移和图像引用。`reachable` 目前是粗略工作区判断，实际路径还需通过 IK 与执行末端误差检查；当前没有通用障碍物避碰规划。

场景使用固定碗、刚体色块和搅拌棒，验收覆盖工具运动与物体接触。液体混合质量、食材形变、真实机器人迁移以及学习型 VLA 控制尚未验证。

## 日志、录像与验证

```text
workspace/sapien/
├── tasks.sqlite3               # 任务、配置快照、事件和最终观测
├── media/
│   ├── sapien-<session>-*.jpg   # 按帧保存，历史图像引用保持稳定
│   └── sapien-<session>.mp4     # 任务录像
└── runs/
    ├── sapien-<session>/
    │   ├── config.json         # 实际物理仿真参数
    │   ├── simulator.log       # 仿真环境和原生库日志
    │   └── evidence.json       # 进程结束时的观测
    └── <CLI 时间>/             # summary.json 与 events.jsonl
```

本机验证记录保存在 `workspace/sapien/environment.json`、`validation.json` 和 `browser-validation.log`。网页自然语言任务 `5967fd1ada88404a826419b95580aa20` 同样完成九步与全部目标，观测搅拌角位移 12.640 rad（约 2.01 圈），记录到 3520 个配料接触物理步；可直接[打开该任务](http://127.0.0.1:8081/?task=5967fd1ada88404a826419b95580aa20)。初次完整 Agent 验证任务 `fed1b95908c24eb79d9c15e2632e841b`：9 步全部成功，6 项目标确认，实际工具角位移 12.846 rad（约 2.04 圈），记录到 3811 个与配料接触的物理步。该数值是一个配置场景的运行证据，不表示随机场景成功率。

现有 20 项集成测试通过；额外提供一个按需运行的真实仿真取消测试，默认测试不会启动 GPU：

```bash
.venv/bin/python -m unittest discover -s tests -v
RUN_SAPIEN_TESTS=1 .venv/bin/python -m unittest tests.integration.test_simulation -v
```

测试验证转运中途取消后停止已确认、夹爪继续持物，并且仿真子进程能够退出。界面另验证了实时画面更新、完整自然语言任务、历史记录、录像、任务链接和手机布局。

## Markdown 技能与本地 VLM 验证

重构后，技能契约、说明与动作程序统一从 `skills/sapien/*/SKILL.md` 加载。文件化技能完成九步任务 `4b089c7f1c1b4b63b3c50bc2b2bbe443`，结果位于 `workspace/skill-md-validation/`。

随后使用真实本地 Qwen 和仿真相机运行非模板指令，任务 [`700d24eea2de41a18a6555815cde5a04`](http://127.0.0.1:8081/?task=700d24eea2de41a18a6555815cde5a04) 完成九步，所有目标经观测确认。模型负责目标解析，规则规划与 Markdown 动作程序负责执行；详情见 [本地 VLM](local-vlm.md)。

### Codex 闭环

正式 Codex 接入后，任务 [`4bb7146732e34feeab3058629253d90a`](http://127.0.0.1:8081/?task=4bb7146732e34feeab3058629253d90a) 完成九步，全程由 GPT-5.5 / medium 解析目标和逐步规划，最终物理观测与模型审阅均成功。详情与限制见 [Codex 接入](codex-integration.md)。
