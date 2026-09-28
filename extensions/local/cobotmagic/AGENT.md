# 通用 Agent 工作台

直接提出问题或下达目标。Agent 根据可用工具和实际结果自行选择处理方式，可以直接回答，也可以观察、定位、分析深度或规划运动。页面不规定工具调用顺序，也不要求用户选择机械臂。

## 两个独立页面

```bash
# 通用 Agent 页面
.venv/bin/python -m primitive_vla_cobotmagic.agent --web

# 独立的手工 IK 标点测试页
.venv/bin/python -m primitive_vla_cobotmagic.ik --web
```

- Agent：http://127.0.0.1:18086
- 手工 IK：http://127.0.0.1:18085

远程 IDE 转发对应端口即可访问。两个进程独立运行；Agent 页面不内嵌手工 IK 控件。可以用 `--port` 修改监听端口，顶部 IK 链接默认指向同主机的 18085。

Agent 页提供任务输入、实际工具调用记录、动态图像标签、回答、任务取消和历史记录。工具参数和返回值可以展开查看。已开放 `robot.execute`：Agent 可执行当前任务生成的有效计划，不需要复制命令或按空格。轨迹统一 3 秒，实际到位由反馈确认，不能把发送完成当成到位。新观察、重新检测或新的规划尝试会作废此前待执行计划。

消息默认接续当前会话：保留用户消息、回答、工作记忆和工具结果，较早的结果可通过 `session.search` / `session.read` 检索。网页显示多轮对话，刷新或重启服务后仍可从最近会话继续；“新建会话”清空本次对话上下文。每轮单独记录执行状态与取消结果，历史动作不会重放。旧观察与计划是历史证据；新一轮运动必须重新观察并生成当前有效计划。仅打开页面不会读取 ROS、加载视觉模型或调用模型服务；提交任务后才根据 Agent 的决定调用工具。

## 解除取消后遗留的控制锁

1. 确认现场机器人已经停稳，退出其他 VLA、遥操作等控制程序；ROS 桥保持运行。
2. 网页出现“控制已锁定”时，点击 **机器人已停止，恢复控制**，确认提示。
3. 程序结束旧 VLA worker，只读取 ROS 状态：两臂关节时间戳持续更新，并在至少 1 秒内保持稳定，才解除锁。失败原因显示在按钮下，可处理后重试。
4. 看到“控制已恢复”后重新发送任务。旧任务、旧计划、夹爪状态和复位需求不会被重放或清空。

恢复入口是人工网页操作 `POST /api/control/recover`，不注册为 Agent 工具。恢复过程不发送动作、不使能电机、不复位机械臂；锁记录和恢复证据写入原任务日志。不要通过删除任务数据库来解除锁。

## 可用能力

目前配置提供相机观察、GroundingDINO + SAM 定位分割、Depth Anything 单目预测、像素 IK 规划与真实运动执行。可用工具的描述、参数和结果是 Agent 的调用依据，系统提示不把它定义为“选臂/IK 专用 Agent”。

`camera.observe` 给 Agent 返回图像、相机位置（`front` / `fixed_front`）和 `observation_id`。关节向量、文件缓存、IK 配置和补偿参数由程序内部管理，不随观察结果灌入模型上下文。`robot.arm_state` 只返回可用臂、待复位臂和 IK 准备标识，不展开整套复位配置。

通常直接调用 `perception.segment({"query":"blue cup"})` 或 `perception.depth({})`：复用当前画面，没有当前画面时自动取图。需要绑定具体画面时可显式传 `observation_id`，也兼容当前取图调用的 ID；刷新前或真实运动前的旧标识不能指向新画面。分割和深度结果携带对应前视图，缓存文件名保留在工具内部。

规划工具的 `arm` 参数由 Agent 填写，左右臂各自使用对应标定与补偿；用户不用选臂。模型可以分别试算两臂；每次尝试替换待执行计划，最终应保留选中的可行计划。检测结果和 IK 使用同一次图像与关节快照，模型不能凭空填写检测 ID 或 XYZ。

分割结果同时返回 `arm_context`：两臂标定坐标下的物体位置、水平距离和 `preferred_arm`。默认距离差超过 5 cm 才给出明确的就近建议（`agent.yaml` 的 `arm_preference_distance_margin_m`）；中央区域或标定不足时不强行指定。该建议供 Agent 结合场景、用户指定和持物状态判断，不绑定 VLA 的双臂输出，也不声称已验证可达性或碰撞。操作 SKILL 要求同侧就近优先，不能仅因同侧一次局部 IK 失败就换另一臂跨过去。继续用同一条臂时不为清空 `needs_reset` 而额外复位。

Agent 的规划返回只保留决策与执行所需字段；完整像素轨迹仍在规划目录的 `review.json`，不再用大量绘图坐标挤掉 `plan_id` 和失败原因。

工具定义仍明确区分预测、规划和真实执行。Depth Anything 输出米制预测，但没有 RGB-D 真值来验证精度，因此不参与实际运动坐标计算。IK 继续使用 `ik.yaml` 中的高度与补偿；默认是所选臂 base 上方 17 cm，不是自动测量碗口上方距离。

## 配置与复用

- `configs/agent.yaml`：组合模型服务、IK 配置和已有感知环境/权重；可选 `system_prompt` 可替换通用提示。
- `configs/cameras.yaml`：共用模型服务设置与密钥环境变量。
- `configs/ik.yaml`：高度、补偿、朝向、时长和标定工作区。

默认读取根目录 `.env`，不覆盖已有环境变量。视觉推理直接使用 `python_executable` 指定的已有 `robotwin` 环境，代码由本扩展的 `perception/` 提供。`models_dir` 沿用 `workspace/tabletop-models` 中的权重，目录名只表示历史存放位置；不复制模型、不创建新环境、不自动下载，也不初始化 SAPIEN。部署到其他机器时，将这两个配置项指向自己的 Python 环境和权重目录；感知依赖声明在本扩展的 `perception` 可选依赖中。

模型输出额度在 `configs/cameras.yaml` 的 `model.options.max_tokens` 配置，当前为 16384。DeepSeek 的思考和最终 JSON 共用该额度；4096 曾导致思考耗尽额度、返回空 content。JSON 格式错误由框架单独限次修复，不消耗正常决策轮数；仍失败时网页显示具体原因和最近进展。暂停后可在同一会话说明已完成的部分并继续剩余步骤，不必重新发起整个任务。

网页数据保存在 `workspace/cobotmagic-agent/web/`，包含任务数据库、工具记录和图片。模型返回的图像标签动态显示，不限定为三类图片。权重始终在共享目录中。

## 终端调用

```bash
# 普通问题或任务，Agent 自选工具及必要的机械臂
.venv/bin/python -m primitive_vla_cobotmagic.agent --task '观察桌面，有哪些物体？'

# 下达移动目标后，Agent 可自行规划并调用执行工具
.venv/bin/python -m primitive_vla_cobotmagic.agent \
  --task '找到粉色碗，移动到它上方。'

# 不连接 ROS、不调用 Agent API，只测试现有图片的视觉模型
.venv/bin/python -m primitive_vla_cobotmagic.agent \
  --check-image /path/to/front.jpg --query 'pink bowl' \
  --workspace workspace/cobotmagic-agent/offline-probe
```

默认自动选臂。终端保留可选 `--arm left/right`，用于明确需要固定单臂的调试；网页不暴露此选项。`--web` 与 `--execute` 不能同时使用。

普通回答无需生成运动计划就能完成。`--execute` 仅保留旧终端流程兼容；正常 Agent 执行不需要这个参数。已通过执行工具完成的计划不会再由终端流程重复执行。

## 程序位置

- `agent/`：通用任务入口、配置组合、工具、网页与页面资源。
- `perception/`：独立推理进程，包含 GroundingDINO/SAM、Depth Anything 实现及本地权重加载。
- `ik/`：独立手工标点页面及共用规划、终端执行逻辑。

## 执行工具约定

`robot.plan_hover` 返回 `plan_id`；`robot.execute(plan_id)` 只接受当前任务最新的可行计划，一次调用后即消耗。它直接使用现有 ROS 桥，不运行模型拼出的 shell 命令，不改变夹爪开合。规划与执行均固定为 3 秒轨迹，执行结果包含实测 TCP 与误差；驱动实际到位时间仍取决于反馈。

任务取消会停止继续发送轨迹点，并尝试发送当前反馈位置保持指令。现有桥没有停止确认服务；如果动作已经发出但无法确认停止，运行时会如实标记控制状态未释放，不会把任务取消冒充物理急停。执行日志保存在对应计划目录的 `agent-execution.trace.jsonl` 与 `agent-execution.result.json`。

## VLA 和单臂复位

新增 `vla.run(policy_id, preparation_id, duration_s)`、`robot.reset_arm(arm)`、`robot.arm_state()`，仍通过同一个通用 Agent 网页调用。操作约定从 `skills/cobotmagic-manipulation/SKILL.md` 实际注入模型上下文。

- 每次 VLA 前先对目标完成 IK 执行，`robot.execute` 的反馈到位后返回一次性的 `preparation_id`。仅规划不产生凭据；VLA 开始前会再次核对准备臂的实际关节位置。
- VLA 保留模型完整 14 维双臂/夹爪输出，自行选择实际使用的手臂；工具不裁剪动作。IK 准备一条候选臂不等于强制 VLA 使用它，也不保证模型不会使用另一臂。
- 切换操作臂前复位之前用过的另一臂。工具依据工作区 `arm-state.json` 检查，跨聊天轮次保留；VLA 返回的 `used_arms` 来自相对起始反馈的变化。默认关节变化超过 0.03 rad 或夹爪超过 3 mm 记为使用；异常且停止未确认时保守保留双臂复位需求。
- `reset` 参数在 `configs/agent.yaml`。默认选定臂六关节回零，同时让该臂夹爪回到 0 m（闭合），在同一条 3 秒轨迹内插值；另一臂每步读取当前反馈保持。`gripper_position_m.left/right` 可分别配置在 0–0.08 m，设为 `null` 则保持该夹爪。没有碰撞规划，应先放好持有物体再复位。
- 复位的关节到位容差约 5°（`joint_tolerance_rad: 0.0872664626`），夹爪到位容差 5 mm（`gripper_tolerance_m: 0.005`）；启用夹爪复位时，两者反馈均到位才报告成功。复位专用 `feedback_limit_tolerance_deg: 5.0` 允许反馈靠近边界时回零，保持与目标指令仍受现有桥限幅。IK 继续使用 `ik.yaml` 的 2° 反馈容差，不受复位配置影响。
- 复位/IK 执行的预检查失败且未发送指令时保留已有图像和检测；一旦发送过指令（包括随后失败），旧图像、检测和准备凭据失效。
- `vla` 参数也在同一配置中，直接引用现有 Conda 环境、LeRobot 源码、checkpoint 与 HF 缓存。模型懒加载，同一网页进程内常驻，聊天结束只结束本轮控制，不卸载模型；更换策略时释放上一模型。
- `blue_cup_pour` 是拿蓝杯、向碗倾倒、放在碗旁的已有策略。当前默认 30 秒、最大 60 秒是可调执行预算，尚未实测为完整任务时长；20 Hz 和 RTC 参数沿用原脚本。IK 的 3 秒不应用于 VLA。
- VLA 时长从首个动作开始计算，权重加载另有预算。工具返回动作数、停止原因和关节反馈，另保存执行前后三相机图片。`task_success=null` 表示控制器不作语义判断；默认由主 Agent 在原会话正常下一轮中判断目标，不额外调用模型。回复的可选 `assessments` 与下一动作或最终回答一起返回。
- `outcomes` 按动作调用 ID 保存判断来源、理由、证据和修订；不会根据 satisfied/unknown 等状态拦截后续工具或最终回答。主 Agent 决定继续下一目标、观察或重试。网页区分“主 Agent 判断”和“独立验证”，图片可切换执行前后画面。
- `configs/agent.yaml` 中 `verification.mode` 默认 `main_agent`。切换 `independent` 时需同时配置 `reviewer.adapter/options`，可复用相同服务或替换世界模型；独立请求只接收任务和动作证据，不接收主 Agent 的 memory/完整历史。失败仅记录 unknown，不重放动作。独立模式可选 `session.assess` 补充验证；语义准确率仍需真实场景评估。
- 取消会关闭本轮动作循环、停止 RTC，并尝试当前状态保持；反馈连续稳定后才释放控制。无确认时保留控制锁。原终端 `inference_rtc.sh` 与网页不能同时控制机器人。

启动仍是 `.venv/bin/python -m primitive_vla_cobotmagic.agent --web`。启动网页不加载 VLA、不连接 ROS、不自动执行操作。

默认配置：

```yaml
verification:
  mode: main_agent
```

如需单独请求 DeepSeek 验证，可将该段替换为以下配置；options 可使用 `cameras.yaml` 中已有的模型服务参数，密钥继续从环境变量读取：

```yaml
verification:
  mode: independent
  reviewer:
    adapter: primitive_vla.verification.model:JSONOutcomeReviewer
    options:
      base_url: https://api.deepseek.com
      model: deepseek-flash
      api_key_env: DEEPSEEK_API_KEY
      require_api_key: true
      timeout_s: 120
      max_images: 6
      max_tokens: 16384
```

也可将 adapter 改为自己实现的世界模型。模式切换在新任务创建时读取；修改 Python 代码后须重启网页服务。通用契约见仓库 `docs/verification.md`。

## 任务 SKILL

`configs/agent.yaml` 的 `skills` 列表指定要加载的任务说明，路径相对配置文件解析；共同的 `cobotmagic-manipulation` 操作约定始终加载。当前任务：

| SKILL | VLA policy_id | 目标 |
| --- | --- | --- |
| `skills/pick-bowl/SKILL.md` | `pick_bowl` | 抓起碗，放到桌面中央并松爪 |
| `skills/pick-cup-blue/SKILL.md` | `blue_cup_pour` | 抓蓝杯，向碗内倾倒，再放到碗旁并松爪 |

蓝杯的数据任务名是 `pick_cup_blue`，工具 ID 沿用已有 `blue_cup_pour`。两者都直接引用原 checkpoint；任务 SKILL 不触发自动运动，也不为没有请求的任务追加动作。
