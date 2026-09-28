# Cobot Magic 扩展

双臂统一标定网页：运行 `.venv/bin/python -m primitive_vla_cobotmagic.calibration web`，打开 http://127.0.0.1:18084 。共用相机内参、左臂外参、右臂外参分别保存，支持新建批次、键盘采样和网页求解。操作流程与后续还需标定的项目见 [CALIBRATION.md](CALIBRATION.md)。整个流程只读取图像和关节反馈，不包含机器人运动控制。

本扩展默认连接 `10.192.73.147:48055` 的 MagicBot TCP→ROS2 桥。源码、配置、SKILL、文档、测试和参考 URDF 随主仓库管理；环境、密钥、权重、标定结果、运行记录和下载的可视化网格继续忽略。

`configs/cameras.yaml` 仅注册只读相机工具；`python -m primitive_vla_cobotmagic.agent --web` 使用包含感知、IK、VLA 和复位的 Agent 工具集，可执行真实动作。独立 IK 网页只生成计划，终端脚本由用户启动。完整归属与调用顺序见 [控制流与迁移契约](../../../docs/cobotmagic-control-flow.md)。

TCP 固定点标定已接入同一网页：选择“右臂 TCP”或“左臂 TCP”，对准同一点、换朝向采样，程序自动求偏移。具体操作见 [TCP_CALIBRATION.md](TCP_CALIBRATION.md)。

## 使用

以下命令在 Primitive VLA 项目根目录运行。本机已安装；其他环境可安装：

```bash
.venv/bin/python -m pip install -e . -e extensions/local/cobotmagic
```

只需要基础 Agent 依赖和 Pillow，不需要在 Agent 机器安装 ROS、LeRobot、OpenCV 或 GPU 环境。机器人侧继续运行已有的 ROS bridge。

上面的最小依赖适用于相机入口。使用标定、IK 或完整 Agent 时安装 `.venv/bin/python -m pip install -e 'extensions/local/cobotmagic[ik]'`。感知和 VLA 继续使用 `configs/agent.yaml` 指定的独立 worker 环境与已有权重。配置保存了当前部署的地址和路径；克隆到其他机器后需调整路径，并配置该机器的标定工作区。Git 不包含这些环境、权重或标定数据。

不需要 DeepSeek 密钥，直接采集三路相机并生成可打开的 HTML：

```bash
.venv/bin/python -m primitive_vla_cobotmagic.capture \
  --config extensions/local/cobotmagic/configs/cameras.yaml
```

打开 `workspace/cobotmagic-camera/capture.html` 查看前、左、右三路图像。该页面是一次采集的静态快照；再次执行命令会更新页面，历史 JPEG 仍保留。`capture.json` 保存完整 ToolResult，`events.jsonl` 保存调用记录。只读取前相机时加 `--cameras front`，也可以选择 `--cameras left right`。工具会只返回所选图像；桥的现有协议仍一次传输全部图像。

## 交互式视觉问答网页

在根目录 `.env` 中填写 `DEEPSEEK_API_KEY` 后启动：

```bash
.venv/bin/python -m primitive_vla_cobotmagic.web \
  --config extensions/local/cobotmagic/configs/cameras.yaml \
  --port 18083
```

打开 `http://127.0.0.1:18083`。本机的 8083、8084 已被其他服务占用，因此问答页使用 18083；可用 `--port` 指定其他端口。远程 IDE 需要转发这个端口到本机浏览器。

- 页面每 2 秒刷新三路画面，可以暂停自动刷新、手动刷新或点击图片放大。
- 勾选用于提问的相机，输入问题后按 Enter 发送；Shift+Enter 换行。
- 每次提问重新采集所选画面，再发给配置中的 VLM；可连续追问，并随时停止回答。
- 每条回答保留其实际参考图片；后续预览刷新不会替换历史证据。
- 对话保存在 `workspace/cobotmagic-chat/conversation.json`，重新打开网页或重启服务后可以继续查看。每次请求携带最近 12 条已完成问答的文本上下文，以及本次的新图像；历史图片不自动重复发送。
- 设置中可以提供临时密钥，仅保存在当前服务内存中。浏览器状态接口不会返回密钥。

预览帧即时返回浏览器后删除临时文件；用于问答的图像保存在该 workspace 的 `media/` 中。清空对话会清空模型的对话上下文，已有图像文件和模型事件日志保留。页面是共享该 workspace 的单一对话，不是多用户服务；默认绑定本机回环地址。

网页通过 `CameraTools` 采集图像，并复用核心 VLM 传输进行视觉问答。这个页面直接回答图片问题；需要由 Agent 自主选择工具时，仍可使用原有通用控制台：

```bash
.venv/bin/python -m primitive_vla serve \
  --config extensions/local/cobotmagic/configs/cameras.yaml \
  --workspace workspace/cobotmagic-agent
```

打开 `http://127.0.0.1:8083`，输入“读取三路相机，描述桌面上有哪些物体”。工具返回的 JPEG 自动进入现有网页和 JSONModel 的多模态上下文。配置仅包含图像观察工具。

## Interface

```json
{"name":"camera.observe","arguments":{"cameras":["front","left","right"]}}
```

省略 `cameras` 时读取配置中的全部相机。返回 `ToolResult.images`（相机名称 → workspace/media 下的唯一 JPEG 文件名）及 `data`：

- `observation_id`：此次采集引用。
- `bridge_timestamp_ns`：桥生成快照的时间。
- `cameras.<name>`：图像引用、实际宽高、图像 ROS 时间戳、图像在桥处的年龄、包含读取与解码耗时的年龄上界。
- `camera_skew_ms`：所选图像时间戳的最大差值；`synchronized=false`。
- `depth_available=false`：当前桥只提供 RGB。

具体来源是桥的 `images.front/left/right` 和 `image_timestamps_ns`，默认对应 `/camera_f/color/image_raw`、`/camera_l/color/image_raw`、`/camera_r/color/image_raw`。本扩展忽略同一响应中的机器人关节字段。

每次调用使用独立的异步 TCP 连接，超时和取消都会关闭连接。默认连接超时 5 秒、整个请求超时 8 秒。解码前检查报文长度、Base64、JPEG 类型与大小；图像过期、缺失、损坏或桥未就绪时返回错误，不发布不完整的一组图像。

默认 `max_frame_age_s: 1`，并将读取和解码耗时计入保守的新鲜度检查。图像时间戳与桥时间必须兼容；超过 250 ms 的未来时间戳会被拒绝。三路图像来自各自的最新缓存，不是同步采集。桥的现有 `get_observation` 也要求双臂反馈流就绪；只选择一路相机不能绕过机器人侧这个条件。

## 验证

```bash
.venv/bin/python -m unittest discover -s extensions/local/cobotmagic/tests -v
```

感知契约测试使用配置中的感知 Python 环境，以 CPU 和模拟模型输出验证分割、像素中心及深度产物；不加载权重或连接 ROS。当前本机命令：

```bash
PYTHONPATH=extensions/local/cobotmagic/src \
  /home/estar/miniconda3/envs/robotwin/bin/python -m unittest discover \
  -s extensions/local/cobotmagic/tests -p test_perception.py -v
```

测试覆盖报文读取、相机选择、JPEG 保存、过期/缺失/损坏图像、超长/截断报文、超时与取消，并用模拟模型服务验证实际图像 data URL 进入 Agent 上下文。网页测试覆盖连续问答、历史图片保留、预览清理、取消、缺失密钥和图像故障。已在本机完成真实相机 → DeepSeek → 网页回答的在线验证；这只验证功能连通，不代表模型描述始终准确。

## 通用 Agent 网页

运行 `.venv/bin/python -m primitive_vla_cobotmagic.agent --web`，打开 http://127.0.0.1:18086 。直接输入问题或目标，Agent 自主选择工具；需要机械臂时由模型选择左右臂。页面按实际结果显示工具调用、图像与回答，没有预设的识别或 IK 流程。

手工 IK 页面仍独立运行于 18085。Agent 已开放 `robot.execute`，可执行当前任务生成的有效计划，轨迹固定 3 秒，不需要终端按键；结果以实际关节反馈为准。完整说明见 [AGENT.md](AGENT.md)。

## 程序目录

`src/primitive_vla_cobotmagic/` 按功能归组：

```text
calibration/
  cli.py           标定命令行入口
  core.py          标定板、采样、相机内参与手眼求解
  tcp.py           TCP 固定点标定
  workspace.py     双臂批次与工作区管理
  web.py           标定网页服务
  static/          标定页面
ik/
  cli.py           统一 IK 命令行入口
  config.py        参数定义与加载
  pixel.py         图片坐标到冻结计划
  kinematics.py    FK / IK 数值求解
  execution.py     路径规划、桥通信与执行核验
  web.py           只读标点网页服务
  static/          IK 页面
agent/             通用 Agent 网页、配置组合、工具与终端入口
perception/        GroundingDINO/SAM、Depth Anything 与本地权重加载
bridge.py          公共只读 ROS 桥
provider.py        Agent 相机工具
capture.py         相机采集命令
web.py、static/    相机问答网页
```

两个功能目录通过 `__main__.py` 支持原有 `python -m primitive_vla_cobotmagic.calibration` 和 `python -m primitive_vla_cobotmagic.ik` 命令。根目录的 `hover.py`、`ik_web.py` 只转发历史命令，不包含另一套实现。标定复用 `ik/kinematics.py` 的纯数值计算，不加载或调用运动执行模块。

配置仍在扩展的 `configs/`，标定结果和运行记录仍在项目的 `workspace/`；移动源码不改变这些路径。

## 图片坐标 → IK

统一入口是 `primitive_vla_cobotmagic.ik`，参数集中在 `configs/ik.yaml`。网页和命令行共用同一套像素换算、补偿、规划及执行逻辑。

```bash
# 原图像素 U/V；默认只规划
.venv/bin/python -m primitive_vla_cobotmagic.ik --arm right --pixel 320 240

# 规划完成后在终端按空格执行
.venv/bin/python -m primitive_vla_cobotmagic.ik --arm right --pixel 320 240 --execute

# 只读标点网页，打开 http://127.0.0.1:18085
.venv/bin/python -m primitive_vla_cobotmagic.ik --web
```

默认 TCP 高度为所选臂 base 上方 17 cm，图片向上补偿左臂 10 cm、右臂 15 cm，朝下 40–90° 并优先更陡角度，保持两指水平朝向，轨迹时长 3 秒。这些均可配置；执行仍使用现有机器人桥。左右臂的当前内外参和 TCP 标定会自动加载。

完整参数、Python 调用、冻结计划与离线测试见 [IK_WEB.md](IK_WEB.md)。手工 IK 网页不直接执行运动；Agent 页通过执行工具控制真实运动。RGB 换算依赖所设高度平面，本模块不做碰撞规划。

配置目录按用途分为 `cameras.yaml`（相机问答及模型服务）、`calibration.yaml`（双臂标定）、`ik.yaml`（双臂 IK）、`agent.yaml`（引用前述配置与已有感知权重）。左/右臂由网页步骤或 `--arm` 选择，不再各放一份配置。旧 `ik_web` 和 `hover` 命令仍可用于兼容已有工作区记录。
