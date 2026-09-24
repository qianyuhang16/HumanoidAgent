# 桌面识别与抓取

这是独立的本地工具适配包。`extensions/local/tabletop/` 的源码、配置、说明和测试已显式纳入主仓库 Git 版本管理；虚拟环境、密钥、模型权重和运行产物仍被忽略。源码、配置、依赖、下载脚本和专用测试均属于这个包。主项目无需安装本扩展即可运行自身测试。

入口是 [configs/tabletop.yaml](configs/tabletop.yaml)。这套 Adapter 向现有 Agent 注册观察、分割、深度、抓取预测、IK、夹爪和验收工具；Agent 的主循环不包含这些模型或 Panda 的知识。

当前场景使用 SAPIEN、Franka Panda 平行夹爪，支持单物体抓取、双物体分拣和几个可解释的失败场景。运动采用从上方抓取、抬升、转移、放置；尚未实现杂乱场景的整臂碰撞规划、任意构型机器人或完整 6D 抓取。

## 规划与验证展示

真实 Agent 演示（使用配置中的决策模型和 `.env` 中的密钥）：

```bash
.venv/bin/python -m primitive_vla_tabletop.showcase \
  --case sorting --workspace workspace/tabletop-showcase/sorting
```

运行后打开 `workspace/tabletop-showcase/sorting/report.html`。页面包含视频、模型给出的计划分解和更新、每次可行性预检、阶段验收和最终报告，可按“计划拆分 / 可行性 / 目标验证”筛选。原始调用、参数、证据 ID、测量值保留在卡片及 `summary.json`、`events.jsonl` 中。`session.json` 保存模型历史，包括格式纠错反馈。分享报告时需要同时保留 `media/`。

| `--case` | 场景和目标 | 展示重点 |
| --- | --- | --- |
| `sorting` | 红块放入 left 绿色区域，蓝块放入 right 黄色区域 | 自主拆分子目标；第一块完成不等于整个任务完成；最终同时验收两块 |
| `obstructed` | 红块旁存在挡板，要求抓起红块 | RGB-D 可见通道被占据，预检不产生可执行计划；模型解释能力缺口并停止 |
| `anchored` | 红块固定在桌面，外观近似正常；演示指令限定一次抓取尝试 | IK 与动作可能通过，但物体没有抬升；模型读取失败证据、更新计划并报告失败 |
| `empty` | 空桌面，要求抓起红块 | 感知找不到目标，避免凭空产生位置与运动 |
| `clear` | 原有无遮挡单红块 | 正常抓取和持续夹持验收 |

例如：

```bash
.venv/bin/python -m primitive_vla_tabletop.showcase --case obstructed --workspace workspace/tabletop-showcase/obstructed
.venv/bin/python -m primitive_vla_tabletop.showcase --case anchored --workspace workspace/tabletop-showcase/anchored
```

负例预期得到 `blocked` 或 `failed`，命令返回非零退出码；以报告中的具体测量和实际动作记录判读，不把失败状态本身当成代码异常。模型可以选择不同的观察与调用顺序，单次演示不能代表稳定成功率。`anchored` 默认指令限定一次尝试，以突出失败验收；该限制只在自然语言演示任务中，`--instruction` 可以替换它，Agent 和工具没有“一次失败就必须终止”的场景流程。早期不限制尝试的在线运行在失败后反复定位并出现无效 JSON，已保留在 `workspace/tabletop-agent-showcase-anchored*/`，不将其描述成稳定的自主恢复。

`task.plan` 只记录模型自己给出的步骤、依赖、进度和简要依据，不调度动作。`robot.plan_grasp` / `robot.plan_place` 返回 `feasible / infeasible / unknown`；无可执行计划就没有 `plan_id`。预检使用实测 RGB-D 的保守夹爪垂直方形通道，以及端点 IK；每段运动执行前还检查采样路径的 IK 连续性。`observed_cells` 是有效观测覆盖，`occupied_cells` / `occupied_points` 才是障碍占据；预检不保证隐藏几何、整臂避障或抓取一定成功。

`tabletop.verify` 只在评估阶段读取物理真值，同时检查全部任务条件：

- `held`：指定物体抬升超过 8 cm，且与两侧夹指接触。
- `placed`：指定物体完整投影在指定区域内（留 5 mm 裕量），有桌面支持、无夹指接触，线速度小于 0.03 m/s、角速度小于 0.3 rad/s。
- 每次采集 120 个物理步，要求最后至少 110 步连续同时满足全部条件。短暂成功后滑落、抓错对象、只完成一半、悬停在区域上方均不能通过。

本地配置启用了可选 `TabletopFinishCheck`：只有最后一次机器人动作之后的成功验收，并在最终回答引用该调用 ID，才接受 `completed`。它只约束验收证据，不规定模型的执行流程。验收会推进仿真时间，因此之后的感知需要重新观察。

评测目标由场景配置提供，模型不能在失败后将“双物体放置”改成“抓起一个物体”。`sorting` 默认含两个 placed 条件，其他场景默认要求 held 红块；也可以在工具配置的 `options.goal` 中明确设置：

```yaml
scenario: sorting
goal:
  - {target: target, predicate: placed, zone: left}
  - {target: blue, predicate: placed, zone: right}
```

这些都是 tabletop 的评测词汇，通用 Agent 不依赖它们。`showcase --case` 选择预设目标；自定义目标请修改配置后使用通用 `primitive_vla run/serve --config ...`，自然语言任务应与目标一致。删除 `finish_check` 可关闭最终验收门槛。工具能力、任务说明、验收器均在本地扩展中，未把场景流程写进 Agent。

不调用决策模型的回归入口仍为 `demo.py`，用来区分“物理工具链通过”与“模型自主规划通过”：

```bash
.venv/bin/python -m primitive_vla_tabletop.demo \
  --config extensions/local/tabletop/configs/tabletop.yaml \
  --scenario sorting --workspace workspace/tabletop-scripted/sorting
```

2026-09-24 实际决策模型分拣运行：40 次工具调用、4 次模型计划记录、4 次可行性预检和 4 次目标验收；只放好红块时整体未通过，最终两块同时通过 120 步。障碍负例的实际模型运行在预检后报告 blocked，没有调用运动或夹爪。固定物体单次尝试中，全部动作返回 ok，但物体抬升为 0，模型引用验收结果更新计划并报告 failed。

本机展示入口为 `workspace/tabletop-showcase/index.html`。三份实际模型运行分别位于 `workspace/tabletop-agent-showcase-sorting-v2/`、`workspace/tabletop-agent-showcase-obstructed-v3/` 和 `workspace/tabletop-agent-showcase-anchored-v3/`。分拣目录另有 `plans-preview.png`、`verification-preview.png`，可用于汇报截图。

首次在线尝试曾因无效 JSON 中断；核心现将此类错误作为反馈交回模型，并计入轮次预算，格式纠错不会重放已完成的物理动作。HTTP/鉴权错误仍直接报告失败。当前核心测试 54 项、扩展测试 13 项通过；另实测了上述物理与模型演示、空桌面拒绝动作、报告页浏览器筛选和渲染。

模型计划及自然语言解释是模型输出，测量是工具输出。应以原始测量评估是否达标；模型仍可能误读图像或数值。例如早期障碍报告混淆了观测覆盖和障碍占据，随后已拆分字段并补充说明。

## 本机使用

以下命令均在主项目根目录执行。模型权重仍在 `workspace/tabletop-models/`，原有视频和报告仍在 `workspace/`。GPU worker 使用扩展内的 `.venv-worker`，复用本机 RoboTwin 环境的模型依赖；URDF 仍指向已有 Panda 资产。

在项目根目录的 `.env` 填写：

```dotenv
DEEPSEEK_API_KEY=你的官方密钥
```

`.env` 已被 Git 忽略；本扩展附带 [.env.example](.env.example)。CLI 默认读取当前目录的 `.env`，也可以用 `--env-file /path/to/.env` 指定。已经导出的环境变量优先。缺少密钥时，任务会在创建仿真 worker 前失败，并提示需要配置的变量名。

启动 Agent 网页：

```bash
.venv/bin/python -m primitive_vla serve \
  --config extensions/local/tabletop/configs/tabletop.yaml --workspace workspace/tabletop-agent
```

打开 `http://127.0.0.1:8082`，输入“找到桌面上的红色方块，把它抓起来，并确认它被夹爪稳定夹住”。网页显示工具调用、观测图和结束后的视频。每个新任务创建一个新场景，任务结束后释放 worker。

也可以直接运行：

```bash
.venv/bin/python -m primitive_vla run \
  --config extensions/local/tabletop/configs/tabletop.yaml --workspace workspace/tabletop-agent \
  --instruction "找到桌面上的红色方块，把它抓起来，并确认抓取成功。"
```

配置使用 `https://api.deepseek.com` 和模型 ID `deepseek-flash`。这里通过 JSONModel 把工具说明、历史和实际图片发给模型，让它逐轮选择调用；新增规划与验证演示的在线端到端实测记录见上文。

## 不需要密钥的实际模型验证

```bash
.venv/bin/python -m primitive_vla_tabletop.demo --config extensions/local/tabletop/configs/tabletop.yaml
.venv/bin/python -m primitive_vla_tabletop.demo --config extensions/local/tabletop/configs/tabletop.yaml --empty --workspace workspace/tabletop-empty
.venv/bin/python -m primitive_vla_tabletop.demo --config extensions/local/tabletop/configs/tabletop.yaml --seed 13 --workspace workspace/tabletop-seed13
```

这些命令以固定顺序调用与 Agent 相同的工具，用来验证本地模型与物理仿真集成，不调用或模拟 DeepSeek 推理。输出包含 `report.html`、`summary.json`、`events.jsonl`、worker 日志和 `media/` 下的标注图、RGB-D 数据、视频。用浏览器打开 `workspace/tabletop-demo/report.html` 可以直接播放抓取。

2026-09-23 本机实测结果：

| 场景 | 检测分数 | GG-CNN 质量分数 | 物体抬升 | 验收 |
| --- | ---: | ---: | ---: | --- |
| seed 7 | 0.923 | 0.901 | 17.44 cm | 连续 120 步双侧接触和抬升 |
| seed 13 | 0.916 | 0.900 | 17.40 cm | 连续 120 步双侧接触和抬升 |
| 空桌面 | 未接受目标 | — | — | 未发送运动或夹爪指令 |

质量分数不是成功率。两个成功场景不足以说明通用抓取性能。下探结束时 TCP 跟踪误差分别约 2.51 mm 和 2.61 mm，这是对规划位置的控制误差，不是检测定位精度。

## 接入的模型与深度来源

| 能力 | 实现及上游来源 |
| --- | --- |
| 文本定位 | [GroundingDINO tiny](https://huggingface.co/IDEA-Research/grounding-dino-tiny) |
| 分割 | [SAM ViT-B](https://huggingface.co/facebook/sam-vit-base)，用检测框提示 |
| 单目深度 | [Depth Anything V2 Metric Indoor Small](https://huggingface.co/depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf) |
| 抓取预测 | [GG-CNN Cornell 预训练权重](https://github.com/dougsm/ggcnn)，预测平面中心、角度、开口宽度 |
| IK | Panda URDF + SAPIEN Pinocchio，插值笛卡尔路径并检查采样点的 IK、关节限位和关节跳变 |
| 仿真 | SAPIEN 3.0.0b1，相机内外参和 RGB-D，夹爪通过实际碰撞及摩擦夹持 |

这里的 GroundingSAM 是 GroundingDINO + SAM 的组合。GG-CNN 是轻量的预训练平面抓取基线，不是通用 6D 大模型。上游许可证随 `src/primitive_vla_tabletop/GGCNN-LICENSE.txt` 分发；加载的是 `weights_only=True` 的 state dict。

**精确几何默认使用 `depth_mode: rgbd`。** Depth Anything 确实执行推理并输出图像和误差；在 seed 7 场景里，其全图有效像素深度 MAE 约 1.046 m，目标区域中位误差约 1.095 m。因此不能把它的输出直接当成准确的米制抓取深度，更不能把相机测量冒充它的预测。

可以显式测试预测深度：

```bash
.venv/bin/python -m primitive_vla_tabletop.demo --config extensions/local/tabletop/configs/tabletop.yaml \
  --depth-mode depth_anything --workspace workspace/tabletop-predicted-depth
```

当前场景下，这个命令预期以非零状态退出，报告深度误差超过 15 mm，并且不发送动作。预测模式仍用 RGB-D 对目标误差做验证；它不是经过验证的纯 RGB 部署方案。

几何链路为 `像素 + 相机深度 → 相机坐标 → 世界坐标 → TCP 姿态 → IK`。检测、分割、抓取和规划都不读取物体的仿真位姿。物体高度和双侧接触只在 `tabletop.verify` 中作为独立验收依据；没有把物体附着到夹爪或直接修改物体位姿。

## 工具 Interface

| 工具 | 输入 | 返回/约束 |
| --- | --- | --- |
| `tabletop.observe` | 无 | 新鲜图像、`observation_id`、相机帧引用、机器人状态 |
| `perception.segment` | observation_id, query | GroundingDINO/SAM mask；无明确目标时 found=false |
| `perception.depth` | observation_id | depth_ref、深度来源和预测误差 |
| `grasp.propose` | observation_id, mask_ref, depth_ref | GG-CNN 姿态与 candidate_id；拒绝不匹配的观测引用 |
| `task.plan` | rationale, steps | 模型自己制定的计划、依赖、状态与实际调用依据；不执行动作 |
| `robot.plan_grasp` | candidate_id | 可见通道及端点 IK 预检；通过后返回 plan_id 和 approach/descend/lift |
| `robot.plan_place` | grasp_plan_id, observation_id, zone | 目标区域净空及 IK 预检；通过后返回 transfer/lower/retreat 计划 |
| `robot.move` | plan_id, waypoint | 执行一个路点并返回 TCP 跟踪误差 |
| `robot.gripper` | width_m | 0.08 打开，0 关闭，返回实际开口 |
| `tabletop.verify` | 无 | 0.5 秒采样窗口中，全部 held/placed 任务条件的持续验收结果 |

工具声明自己的前置条件：接近、下探之前打开夹爪，抬升之前关闭；移动后旧观测失效。工具不会替 Agent 决定“是否要重新观察、报告阻塞或终止”。`demo.py` 中的固定顺序仅属于测试入口，Agent 不会导入它。

动作 worker 在执行每一段运动时检查截止时间，处理停止请求，并记住已取消和已完成的调用 ID。停止确认必须匹配调用 ID；未确认停止时，现有 ToolRuntime 会锁定进一步动作。路径预检包括工作空间、关节限位和连续性；执行后检查最终位置误差。可见通道检查尚不提供任意障碍物的整臂碰撞保证。

## 换机器或重新下载

主进程安装核心和本扩展，专用模型依赖留在 worker 环境：

```bash
.venv/bin/python -m pip install -e . -e extensions/local/tabletop
```

GPU worker 需要 SAPIEN、PyTorch、Transformers、NumPy、SciPy、OpenCV、Pillow 和 imageio/ffmpeg。已有环境不需要重复安装；新环境可在选定的 Python 中安装 `-e 'extensions/local/tabletop[worker]'`，并按 GPU 选择合适的 CUDA PyTorch。本机验证环境为 Python 3.10、PyTorch 2.9.0+cu128、Transformers 4.55.2、RTX 5090。

本机的 worker 环境按以下方式创建，不修改原来的 RoboTwin 环境，也不复制模型权重：

```bash
/home/estar/miniconda3/envs/robotwin/bin/python -m venv --system-site-packages \
  extensions/local/tabletop/.venv-worker
extensions/local/tabletop/.venv-worker/bin/python -m pip install --no-deps \
  -e extensions/local/tabletop
```

主进程和 worker 都以 editable 方式安装本包，修改扩展源码后下次启动生效。worker 仅运行感知和仿真；主进程通过核心提供的 `JsonProcess` 管理其生命周期。

扩展专用测试：

```bash
.venv/bin/python -m unittest discover -s extensions/local/tabletop/tests -v
```

用 GPU 环境下载权重：

```bash
/home/estar/miniconda3/envs/robotwin/bin/python extensions/local/tabletop/scripts/setup_models.py
```

脚本在 `manifest.json` 记录并复用 HF revision 和 GG-CNN SHA256；显式传 `--refresh` 才更新已记录版本。运行 worker 时只加载本地权重，不自动下载或静默换模型。

更换机器时修改 `python_executable`、`robot_urdf` 和 `models_dir`。`robot_urdf` 必须指向包含碰撞与视觉 mesh 的 Panda 资产；只改 URDF 路径不能把该 Adapter 变成任意机械臂驱动。其他构型应提供自己的工具 Adapter；Agent、会话、网页和模型传输可以继续复用，参见 主项目的 `docs/tools.md`。

源码集中在 [本地适配包](src/primitive_vla_tabletop/)。扩展导入名为 `primitive_vla_tabletop`，配置中的 factory 为 `primitive_vla_tabletop.provider:TabletopTools`。扩展源码已纳入版本管理，新增文件仍需按本地扩展的忽略规则显式添加；通用框架仍保留 `.env` 加载、可选密钥检查及视频展示。
