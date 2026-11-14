# RoboCasa 接入

本集成复用 RPent 的 RoboCasa 环境客户端与程序化动作原语。所有能力通过本框架 Registry、
Router、Hook 和 Memory 调用，不修改 Planning 主循环，不包含任务解题顺序。
当前提供环境、状态、RGB/深度、控制原语和仿真真值验证；不默认加载高层模型或 VLA 权重。

## 环境准备

Python 3.10，Linux，正常可访问的 NVIDIA GPU 与 EGL。
基础框架的安装依赖保持不变，仿真依赖在 [requirements/robocasa.txt](../requirements/robocasa.txt) 单独列出，
固定 RoboCasa 和 robosuite 的源码提交。RPent 只从固定源码加载能力模块，
不安装其整包 Planner/CLI 依赖。不要同时混装其他 RoboCasa 分发包。

```bash
uv venv --python 3.10 .venv
uv pip install --python .venv/bin/python -e '.[gui,test]' -r requirements/robocasa.txt
git clone https://github.com/RLinf/RPent.git workspace/deps/RPent
git -C workspace/deps/RPent checkout b96cbc15d9ba73cf47dc63e3c2512d423bae5365
export PYTHONPATH="$PWD/workspace/deps/RPent${PYTHONPATH:+:$PYTHONPATH}"
.venv/bin/robocasa-download-assets \
  --assets-path "$PWD/workspace/robocasa-assets" --no-macros --skip-existing -y
```

如果使用国内 PyPI 镜像，可保留官方 PyPI 作为补充源：

```bash
uv pip install --python .venv/bin/python \
  --index-url https://pypi.org/simple \
  --extra-index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  -e '.[gui,test]' -r requirements/robocasa.txt
```

`pydantic-ai-slim` 在这里仅用于 RPent 工具声明生成 Schema，不配置模型后端。
已验证能力模块可以在没有 `openai`、`anthropic`、`claude_agent_sdk`、Codex SDK 的环境中导入。
本项目不调用 RPent 的 Planner、CLI 或 Memory 下载流程，也不要求安装这些 SDK。

本机实际下载的六组厨房资产压缩包合计约 **10.2 GiB**，安装后约 **22.8 GiB**。
另有约 1.2 GiB 的当前 Python 环境与约 1.1 GiB 的源码依赖；缓存、压缩包和运行数据另计，
这些数值是本次安装占用，不是理论最低配置。本阶段没有下载神经模型权重，也不需要 RLDX 的检查点。
六组资产均通过 ZIP 完整性校验及官方安装清单检查。物体库与生成纹理使用
[RoboCasa 官方资产仓库的固定版本](https://huggingface.co/datasets/robocasa/robocasa-assets/tree/1b92c3d02ca4354984fec961357db0bff7b32166)，
下载内容的 SHA-256 与官方文件清单一致；厨房设施、基础纹理与 Lightwheel 物体来自上游 Box 链接。
真实 GPU 检查必须在允许访问主机设备的执行环境中进行。2026-11-12 已在本机正常权限下确认
RTX 5090、32607 MiB 显存与 NVIDIA 580.178.04 驱动；受限沙箱内的 `nvidia-smi` 失败不能据此判定主机无 GPU。
最小 MuJoCo/EGL 场景已实际渲染，OpenGL renderer 返回 `NVIDIA GeForce RTX 5090/PCIe/SSE2`。

## 真实环境检查

```bash
.venv/bin/python -m primitive_vla.providers.robocasa \
  --task OpenDrawer --seed 1 --gpu 0 \
  --assets "$PWD/workspace/robocasa-assets" \
  --workspace workspace/robocasa-smoke
```

命令通过真实 Router 采集导航相机与深度，执行 8 步小幅底盘动作，再采集图像并读取成功谓词。
它还通过实际 Tool Schema 拒绝非法步数、夹爪指令和底盘速度，并确认拒绝没有推进仿真。
同时查询当前图像中心像素的世界坐标，再检查动作后旧图像步号已不能用于坐标查询。
检查实际位移、图像变化、资源引用和独立验证记录后，输出 `before.png`、`after.png`、
`smoke-report.json` 与 Memory 文件工作区。`environment_check_passed` 和 `task_success` 分开记录；
此命令不是自主任务演示，也不保证完成 OpenDrawer。

可以用独立工作区检查 `NavigateKitchen`、`PrepareCoffee` 等任务的环境接入。
同一工作区重复检查需显式 `--new-episode`；未结束的调用存在时仍禁止创建新环境。
正式任务评测不得用这个选项把重置计入局部恢复。

## 接入 Agent

宿主在已有事件循环中初始化环境，随后使用原有 Agent。`planner` 由宿主提供：

```python
from pathlib import Path

from primitive_vla.memory import Memory
from primitive_vla.providers.robocasa import RoboCasa, RoboCasaConfig
from primitive_vla.providers.robocasa.provider import ROBOT_TYPE
from primitive_vla.runtime.agent import Agent
from primitive_vla.skills import SkillStore
from primitive_vla.tools.execution import ResourceManager
from primitive_vla.tools.registry import ToolRegistry


async def build_robocasa_agent(planner):
    memory = Memory("workspace/robocasa-agent")
    environment = await RoboCasa.open(RoboCasaConfig(
        task="PrepareCoffee", seed=1,
        assets_path=Path("workspace/robocasa-assets"),
        workdir=Path("workspace/robocasa-agent/native"),
    ), memory)
    registry, resources, skills = ToolRegistry(), ResourceManager(), SkillStore()
    environment.register(registry, resources, skills)
    agent = Agent(planner, registry, memory, skills, resources)
    return agent, environment
```

创建任务时使用配置中的 `robot_id`（默认 `robocasa-1`）和 `ROBOT_TYPE`。
任务指令应结合环境提供的 `task_language`，不能只依据类名猜完整目标。
需要界面时把这个 Agent 传给 `create_app`，配置对应 `RobotProfile`，并传入
`view_sources={environment.config.robot_id: environment.views}` 显示当前相机画面。
预览的接口及生命周期见 [机器人视角](robot-views.md)。正式采集产生的证据仍使用 Memory 资源接口。
模型适配器仍须把图片字节实际传给模型；资源 ID 不是视觉输入。

关闭时先通过 `TaskService.close()` 或 `Agent.close()` 停止并排空调用，再 `await environment.close()`。
环境在动作未排空时拒绝关闭；不得通过杀线程伪装停止确认。

## 能力及语义

| 名称 | 说明 |
| --- | --- |
| `state.robocasa.observe` | 读取最近已完成仿真步的状态缓存；动作期间可读，附时间、步号和 episode ID |
| `state.robocasa.capture_image` | RGB PNG 及可选深度 NPY，保存为 Memory 资源引用 |
| `state.robocasa.localize_pixel` | 将指定相机像素按深度投影到世界坐标，检查观测步号；不做目标识别或可达性判断 |
| `execution.robocasa.move_to / move_delta` | 世界坐标目标或相对末端位移 |
| `execution.robocasa.rotate_pitch` | 腕部旋转原语，具体语义见 Tool Schema |
| `execution.robocasa.set_gripper / release` | 夹爪操作 |
| `execution.robocasa.scripted_grasp` | 局部抓取控制序列，不代表抓取效果已被验证 |
| `execution.robocasa.move_base / navigate_to` | 局部底盘控制；不提供全局路径规划、SLAM 或语义寻物 |
| `verification.robocasa.task` | 原生 `_check_success()` 及进度、独立 JSON 证据，注明仿真真值来源 |

RPent 的参数说明与 Schema 用于注册原语；本集成在调用入口约束步数、正距离阈值、底盘速度和夹爪取值，
内部直接调用已验证的处理器。新增准备与恢复知识可通过 Skill 扩展，无需修改 Agent。

## 生命周期与边界

- 环境创建、动作、相机采集与清理都由一个专用线程执行，保持 MuJoCo/EGL 线程归属。
  读取状态缓存不触碰 MuJoCo；显式 `capture_image` Tool 排在当前动作之后。
  桌面预览使用独立只读订阅，空闲时定时采集、动作期间在仿真步之间限频采集，目标 3 FPS，
  边长为 `min(image_size, 512)`。同相机的订阅共享采集，慢消费者只保留最新帧；退订后停止额外渲染。
  预览不推进仿真、不写入 Memory，采集异常不会把动作转为失败。
- 所有推进仿真的原语占用同一实例的 `simulation` 资源，避免底盘与手臂调用同时推进环境。
- 取消在步间检查。若当前同步仿真步尚未返回，则保留占用和 `stop_unconfirmed`；工作线程实际结束后才释放。
  该证明依赖本地同步模拟器，不可直接推广为真实机器人或远程控制服务的停止协议。
  在 Provider 入口被拒绝、尚未提交原生动作的调用可以直接确认停止；未知或恢复的调用不能据此确认。
- 同一进程的暂停/恢复保留物理状态；进程重启不恢复 MuJoCo 状态。旧调用不会重放，
  新建 episode 后旧 Session 的工具请求会被拒绝。当前没有物理状态检查点恢复。
- 原语返回 `ok`、动作结束和任务成功分开记录。原生谓词异常属于验证调用失败，不伪造 failure/success 结论。
  关联动作必须已结束且来自同一 episode、Session、Task。
- 当前核心仍允许 Planner 自行报告成功，详见此前用户故事中的成功证据约束缺口。
  评测应以独立 Verification 记录为准，不能用 Planner 的文字结论代替它。

## 验证状态

- 12 项替身测试已验证 Provider 边界、资源互斥、取消、证据关联、资源存储和防止跨 episode 恢复。
  它们不证明真实物理动作或模型自主性。
- 2026-11-12，RTX 5090 上的 `NavigateKitchen / target / seed=1` 真实检查已通过：
  128×128 RGB 与米制深度采集、像素世界坐标投影、过期图像拒绝、非法参数无动作拒绝、
  8 步底盘控制、图像变化和独立成功谓词记录。
  实测底盘位移约 1.59 mm。报告位于 `workspace/robocasa-navigate-smoke/smoke-report.json`。
- `OpenDrawer` 与 `PrepareCoffee` 的相同接入检查也已通过，均使用完整资产、`target / seed=1`；
  报告分别位于 `workspace/robocasa-smoke/smoke-report.json` 和
  `workspace/robocasa-coffee-smoke/smoke-report.json`。三个场景均注册同一组 12 个 Tool，
  不需要修改 Planning 主循环。汇总见 `workspace/robocasa-checks/integration-summary.json`。
- 同一场景的额外真实检查验证了：互斥控制被拒绝、动作期间状态缓存可读、取消经历
  `stop_unconfirmed → cancelled` 后释放资源，仿真停在第 5 步且没有继续推进。
  本次停止确认约 32 ms，仅为该次观测，不是时延承诺。夹爪与末端位移原语执行完成；
  步数不足的导航返回 `execution_error`，资源仍能正确释放。
  报告与 256×256 图像位于 `workspace/robocasa-behavior-check-2/`。
- 以上检查的任务谓词均为 `failure`，因为没有执行到各自任务目标；这同时验证了动作完成不等于任务成功。
  它们是接入与运行边界检查，不是 VLM 自主任务成功率、RPent 成绩复现或真机验收。
- 完整 Python 回归共 70 项通过；wheel 构建及其中 Provider、Skill 文件完整性检查通过。
- 测试命令：`.venv/bin/python -m unittest tests.test_robocasa -v`。
  执行环境需允许 asyncio 的本地跨线程唤醒；本次受限沙箱会阻塞该唤醒，正常本机权限下已通过。

## RoboCasa 桌面装配

先完成 [RoboCasa 环境准备](robocasa.md)。在 **Git 忽略的** `workspace/deepseek/config.json`
中设置本地配置，密钥也可通过 `DEEPSEEK_API_KEY` 环境变量提供：

```json
{
  "api_key": "填入本地密钥，或删除此项并设置环境变量",
  "model": "deepseek-flash",
  "workspace": "workspace/deepseek/robocasa",
  "task": "OpenDrawer",
  "assets_path": "workspace/robocasa-assets",
  "seed": 1,
  "gpu": 0,
  "image_size": 512
}
```

配置文件权限设置为 `600`，不把密钥复制到代码、测试、报告或任务输入中。
`PRIMITIVE_DEEPSEEK_CONFIG` 可以指定其他本地配置文件。连接官方服务时会发送任务上下文及
模型明确请求的图像；API 密钥只用于认证请求头。

在图形桌面的终端启动：

```bash
bash scripts/start-deepseek-desktop.sh
```

该入口加载 ModelPlanner + DeepSeekVLM、RoboCasa 的 12 个工具与操作 Skill。环境初始化结束后才接受任务。
初始化与关闭通过应用 lifespan 管理；退出时先停止 Agent 调用，再关闭 MuJoCo/EGL 环境。
默认 `.venv/bin/primitive-vla desktop` 仍是原来的草稿入口。

同一进程内可以暂停和恢复。进程重启后，RoboCasa 不能恢复旧物理状态；需要明确创建新 episode：

```bash
PRIMITIVE_ROBOCASA_NEW_EPISODE=1 bash scripts/start-deepseek-desktop.sh
```

此操作沿用原来的停止确认与 episode 隔离检查，不恢复旧任务的物理进度，也不把重置当成失败恢复。
已有工作台窗口需要正常退出后再换启动配置，单实例启动不会热替换正在使用的 Agent。


旧工作区已删除，启动时直接创建文件记忆目录。已有文件记忆不代表仿真物理状态已恢复，
重启仍须显式新建 episode。
