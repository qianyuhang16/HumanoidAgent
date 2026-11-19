# 通用具身 Agent

以可替换的 VLM 进行决策，用 Markdown Skill 描述能力，Tool 执行具体操作，Memory 保存证据与任务信息，通过事件驱动完成观察、执行和验证。框架不预设抓取、导航或搬运工作流。

## 本次更新：2026-11-20

- 读图工具直接向模型返回真实图片；模型使用 image_1 等稳定引用，不搬运 UUID、时间戳、场景 ID 或步号。
- 模型上下文与内部审计分开，保留用户要求、动作参数、结果和证据，减少重复状态与高频进度。
- Skill 正文与注册配置分开；输入只包含必要选择。图像引用解析、Skill 正文拆分分别收敛到注册/加载入口。
- 桌面支持连续对话与调用详情两种视图。任务失败、完成或暂停后可补充消息继续，沿用历史和预算。
- 修复只返回图片的动作丢失完成状态、Skill 正文被错误截断、合法布尔 Schema 使引用解析崩溃的问题。
- RoboCasa 分支新增台面抓取与双食材入锅组合包，更新像素定位接口并提供脱敏运行配置。

**准备合并的开发者先读 [本次更新与合并说明](docs/updates/2026-11-20.md)。** 其中包含接口迁移表、高冲突文件、分支边界、同事 chen 分支包装层需要保留图片附件的位置，以及验证范围。

## 分支与目录

| 分支 | 内容 |
| --- | --- |
| main | 通用框架、独立桌面、VLM API 配置、Tool/Skill 装配和通用测试；不带 RoboCasa 运行包 |
| [feat/robocasa](https://github.com/qianyuhang16/HumanoidAgent/tree/feat/robocasa) | 通用框架加 RoboCasa 平台源码、skills、profiles、脱敏运行配置和安装说明 |

```text
src/primitive_vla/
├── planning/             # 决策接口、模型上下文
├── vlm/                  # 独立 DeepSeek / GPT 通信与配置
├── runtime/              # 会话、预算、生命周期、平台连接
├── tools/                # 注册、装配、三个工具域与控制互斥
├── memory/               # 文件记忆、原始 journal、资源
├── skills/               # Markdown 解析
└── interfaces/           # 本地 API 与桌面前端
desktop/                  # Electron 窗口
configs/*.example.json    # 可提交示例；真实配置不提交
skills/README.md          # Skill 编写规范
docs/                     # 架构、接口、使用与合并说明
```

平台包在自己的 platform.json、profiles/、skills/ 中声明环境和能力，宿主不扫描 workspace。平台可以放在任意路径。模型配置与平台启动配置独立，见 [装配说明](docs/assembly.md)。

## 安装通用框架与桌面

需要 Python 3.10+、Node.js 22.12+（推荐 24）和可用的桌面图形会话。核心与草稿桌面不需要 GPU、模型权重或机器人 SDK。

```bash
git clone https://github.com/qianyuhang16/HumanoidAgent.git
cd HumanoidAgent
git switch feat/robocasa
python3.10 -m venv .venv
.venv/bin/python -m pip install -e '.[gui,vlm,test]'
npm --prefix desktop ci
npm --prefix desktop run setup
```

setup 下载锁定的 Electron 运行时，需要联网。仅使用核心时可只执行 `pip install -e .`，不安装桌面依赖。

先启动未连接机器人的独立桌面：

```bash
.venv/bin/primitive-vla desktop
```

该入口提供真实草稿、历史和资源浏览，不会自动装配测试模型或仿真机器人。
Linux 图形会话、打包和后端连接排查见 [桌面文档](docs/desktop.md)。

## 连接模型与平台

首次配置模型时执行下列命令；已有 configs/vlm.json 时保留原文件。

```bash
cp -n configs/vlm.deepseek.example.json configs/vlm.json
chmod 600 configs/vlm.json
read -rsp 'DeepSeek API Key: ' DEEPSEEK_API_KEY
printf '\n'
export DEEPSEEK_API_KEY
```

也可使用 configs/vlm.gpt.example.json，填写账号可用且支持图像输入的模型名称，并设置 OPENAI_API_KEY。
示例只包含环境变量名，不含真实密钥；模型参数与网络配置详见 [VLM 文档](docs/deepseek.md)。

再按平台提供方的说明准备 configs/runtime.json：connection.command/args/cwd/env 决定如何启动平台，profile 决定能力组合，run_dir 保存任务数据。模型文件不包含平台配置。

```bash
bash scripts/start-desktop.sh configs/vlm.json configs/runtime.json
```

当前分支的 RoboCasa 安装与启动方式见下文；main 不提供 RoboCasa 本地运行配置。更换模型或 profile 需要重新启动，当前不支持热切换。

## 客户端如何使用

1. 确认 Runtime 已连接并选择机器人，输入自然语言目标，点击发送。
2. 对话视图阅读 Agent 回复；切到调用详情查看实际参数、返回值、错误和验证证据。
3. 需要纠正目标或方向时，先暂停正在运行的决策，再在原会话补充消息。追加消息保留历史与累计预算。
4. 暂停只停止 Planning；需要结束动作时请求停止，并等待平台确认。关闭窗口也必须完成停止确认。
5. 右侧机器人视角是只读预览。模型真正看图通过正式采图 Tool；看到桌面预览不等于模型已经获得新图。

普通看图无需像素坐标。只有像素定位工具需要类似 `{"image":"image_1","row":120,"column":86}` 的参数；row 从上往下、column 从左往右，均从 0 开始。真实帧信息由系统解析，平台检查是否过期。

执行 completed 只表示工具结束，目标是否达成应查看独立 Verification。当前框架仍允许 Planner 报告结果，不能把文字报告当成独立效果验证。任务预算由 runtime/session.py 中 Limits 定义；取消、预算耗尽或停止未确认的任务不通过追加消息绕过限制。

## RoboCasa：安装、场景与启动

当前 feat/robocasa 分支包含接入包。新机器在完成前面的桌面安装后，还需在 Python 3.10 环境中安装仿真依赖、固定 RPent 源码和资产：

```bash
.venv/bin/python -m pip install -r workspace/robocasa/requirements.txt
mkdir -p workspace/robocasa/deps
git clone https://github.com/RLinf/RPent.git workspace/robocasa/deps/RPent
git -C workspace/robocasa/deps/RPent checkout b96cbc15d9ba73cf47dc63e3c2512d423bae5365
.venv/bin/robocasa-download-assets \
  --assets-path "$PWD/workspace/robocasa/assets" --no-macros --skip-existing -y
```

需要 Linux、NVIDIA 驱动/EGL，先在正常主机终端确认 nvidia-smi 可用。已有环境与资产可复用；uv 环境安装命令、容量、GPU 和路径问题见 [完整安装与排查](docs/robocasa.md)。不需要下载 VLA 权重。

准备好前述 configs/vlm.json 和 API Key 后，选择下表第二参数启动：

| 场景 / profile | 运行配置 | 可用 Skill |
| --- | --- | --- |
| 开抽屉 / open-drawer | configs/runtime.robocasa.example.json | 12 |
| 做咖啡 / prepare-coffee | configs/runtime.robocasa-prepare-coffee.example.json | 12 |
| 厨房导航 / navigate-kitchen | configs/runtime.robocasa-navigate-kitchen.example.json | 6 |
| 台面抓取 / tabletop-grasp | configs/runtime.robocasa-tabletop.example.json | 12 |
| 双食材入锅 / two-vegetables-in-pan | configs/runtime.robocasa-two-vegetables.example.json | 12 |

```bash
bash scripts/start-desktop.sh configs/vlm.json configs/runtime.robocasa-tabletop.example.json
# 或双食材入锅：
bash scripts/start-desktop.sh configs/vlm.json configs/runtime.robocasa-two-vegetables.example.json
```

客户端新建对话，输入“抓起台面上的柠檬，抬高至少 8 厘米并保持夹持，验证是否成功”，或“双食材都放入锅内，分开放置，完成后验证”。模型自主选择观察、动作和恢复工具；场景配置没有动作顺序。

workspace/robocasa/platform.json 管理本平台环境，profiles/ 管理场景和能力组合，skills/ 包含每个工具的独立 Markdown，runtime/ 实现能力。模型配置在平台包外。上传的 config 均为相对路径和环境变量名组成的 example，真实密钥、资产、deps 和 runs 不上传。

开抽屉示例首次启动使用 new_episode=false；其他四份配置显式 new_episode=true。进程重启不恢复物理场景：新 episode 应新建会话，未确认停止的调用不能通过重启绕过。完整能力边界、像素引用、运行目录、日志、场景成功判据和故障排查见 [RoboCasa 使用手册](docs/robocasa.md)，源码布局见 [平台 README](workspace/robocasa/README.md)。

## 开发与测试

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -t . -v
PRIMITIVE_PYTHON="$PWD/.venv/bin/python" npm --prefix desktop test
git diff --check
```

测试使用替身及本地子进程，不消耗模型 API 费用、不控制真实机器人。测试涉及本机回环服务，受限沙箱可能无法运行，应在正常本地环境验证。
本次完整平台分支验证为 156 项 Python 测试、18 项桌面测试通过；不含 RoboCasa 的主线独立工作区通过 139 项 Python 测试、18 项桌面测试。最新图片引用接口没有重新完成真实模型/GPU 自主任务验收。

修改前阅读 [Agent.md](Agent.md)。新增能力提供 skill.md 与对应实现，不修改 Planning 主流程；工具入口校验契约，内部使用已验证数据。低概率防御、兼容层或重试不能无依据增加。

## 文档入口

- [更新与合并冲突处理](docs/updates/2026-11-20.md)：本次文件职责、接口迁移、同事分支适配与验证边界。
- [安装、启动与桌面操作](docs/desktop.md)：独立窗口、视角、任务控制与打包。
- [模型配置和平台装配](docs/assembly.md)、[VLM API 配置](docs/deepseek.md)：两组独立配置及工厂入口。
- [扩展契约](docs/extensions.md)、[Skill 规范](skills/README.md)、[平台协议](docs/platform-protocol.md)：如何接入新平台和工具。
- [架构](docs/architecture.md)、[Memory](docs/memory.md)、[机器人视角](docs/robot-views.md)：模块边界与生命周期。
- [验收记录](docs/acceptance.md)、[用户故事](docs/user-stories.md)、[评测选型](docs/agent-evaluation.md)：历史证据和未实现能力。
- [RoboCasa 安装与运行（平台分支）](https://github.com/qianyuhang16/HumanoidAgent/blob/feat/robocasa/docs/robocasa.md)。

本仓库不上传 API Key、configs 下的真实配置、机器人资产、上游依赖检出、运行日志和视频。只提交 *.example.json；不要强制添加被忽略的目录。
