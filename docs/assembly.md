# 工作区平台与 Skill 装配

模型配置、接入平台、运行配置各自独立。框架不内置平台列表，不导入机器人 SDK。
`application.create_workbench` 根据文件配置创建 VLM、发现平台，最后装配同一个 ModelPlanner 和 Agent。
当前在启动时选择配置；桌面运行中的热切换尚未实现。
下文使用 RoboCasa 举例，相关平台文件及配置示例在 `feat/robocasa` 分支；`main` 仅提供通用装配能力。

核心职责分属两个现有模块：

- `tools/assembly.py`：`AssemblyCatalog` 发现并选择平台/运行配置，`RunProfile.load_skills()` 加载所选工具文档并检查声明依赖。
- `runtime/connections/`：`open_platform()` 启动平台进程，建立工具和视角通信，并管理关闭。

装配层不依赖 Runtime，也不启动进程。Skill 缺少声明依赖时在启动前报错；实际实现入口由平台进程加载。
`application.py` 调用两者并创建 Agent，工作区配置字段仍为 `assembly`。

## 工作区结构

```text
workspace/robocasa/
├── platform.json         # 启动命令、软件环境、平台默认参数
├── profiles/*.json       # 场景参数 + 本次选择的 Skill 文件
├── skills/<域>/<工具>/skill.md  # 每个工具的声明、入口与使用说明
├── runtime/              # 平台实际接入代码，在独立进程执行
├── requirements.txt      # 仿真依赖
├── config.json           # 本机模型和装配选择，Git 忽略
├── assets/               # 资产，Git 忽略
├── deps/                 # 上游源码，Git 忽略
└── runs/                 # 实际任务数据，Git 忽略
```

平台源码、声明、运行配置和 Skill 纳入版本管理；密钥、数据、缓存不提交。
新增平台可以新增同级目录，或把 `platforms` 指向另一组平台目录，不必修改核心或注册 Python 工厂。

## 第一层：接入平台

`platform.json` 示例：

```json
{
  "id": "robocasa",
  "label": "RoboCasa",
  "connection": {
    "transport": "stdio",
    "command": "../../.venv/bin/python",
    "args": ["-m", "runtime.server"],
    "cwd": ".",
    "env": {"PYTHONPATH": "deps/RPent:.", "MUJOCO_GL": "egl"}
  },
  "options": {"assets_path": "assets", "gpu": 0, "image_size": 512}
}
```

`command` 选择已安装依赖的运行环境，可以换成该平台自己的虚拟环境。框架不会自动安装依赖。
含路径的 command 相对于平台目录解析，保留虚拟环境解释器符号链接；裸命令按 PATH 查找。
`cwd` 相对于平台目录；args 作为参数数组直接传递，不经过 shell；env 在继承环境上覆盖。
平台业务参数的路径语义由平台解释，例如 RoboCasa 的 assets_path 相对于子进程 cwd。
配置是本机可执行配置，应由工作区所有者维护，不能把外部文本当成启动命令。

一个平台启动一个持续运行的进程。宿主解析运行配置选中的 skill.md，并把工具声明传给进程。
通用协议模块按文档声明加载实现并注册，初始化响应报告机器人、实际工具、控制资源和可选视角。
框架把远程工具注册到现有 Router，仍由 Execution 统一进行资源互斥，调用记录仍经 Hook 进入 Memory。
stdout 只承载协议，诊断进入每次运行的 `native/platform.log`。二进制证据通过文件传递并按引用导入 Agent Memory；
低帧率预览单独传输，不写入 Memory。协议见 [本地进程接口](platform-protocol.md)。

## 第二层：运行配置

每个 `profiles/*.json` 是一个独立选择项：

```json
{
  "id": "open-drawer",
  "label": "开抽屉",
  "environment": {"task": "OpenDrawer", "seed": 1},
  "skills": ["skills"]
}
```

Skill 路径相对于平台目录，也可以指向共享 Skill 目录中的文件。
仿真配置可以声明 task、seed；真机配置可以声明设备参数和 Skill 组合，不强制真机拥有仿真任务字段。
参数合并顺序为平台 options → 本机 assembly.options → profile.environment；本机选项不能覆盖 profile 已声明的字段。
Profile 没有动作列表，也不会启动一条预设业务流程。

## 每个工具的 Skill

每个工具有独立 `skill.md`，YAML 元信息是工具名称、Schema、资源、超时和实现入口的唯一来源。
正文记录该工具的条件、准备、调用、验证和恢复方法。完整示例见 [Skill 规范](../skills/README.md)。

```text
skills/
├── state/
│   ├── observe/skill.md
│   ├── capture_image/skill.md
│   └── localize_pixel/skill.md
├── execution/
│   ├── move_to/skill.md
│   ├── move_delta/skill.md
│   ├── rotate_pitch/skill.md
│   ├── set_gripper/skill.md
│   ├── release/skill.md
│   ├── scripted_grasp/skill.md
│   ├── move_base/skill.md
│   └── navigate_to/skill.md
└── verification/
    └── task/skill.md
```

配置选择目录时递归扫描其中的 skill.md，选择文件时只加载该文件。
开抽屉和咖啡配置选择整个 skills 目录，注册 12 个工具及同名 Skill；导航配置选择 State、底盘移动、导航和验证，共 6 个。
这些是可用能力集合，执行顺序由 Agent 决定。

注册链路为：profile.skills → SkillStore 解析 → tool_definitions → 子进程通用 register_tools → Agent 远程工具。
平台环境代码不提供注册表或工具名称清单；`runtime/environment.py` 只提供工具实现，`backend.py` 调用原生 SDK。
`entrypoint` 决定加载哪个实现，`options` 提供该实现需要的配置，不在框架中分支判断具体工具名称。

新增、重命名、移除工具或修改参数契约由对应 skill.md 控制，重启后生效。
新增算法仍需实现能力代码，但不需要修改注册清单、Agent 主流程或其他工具的文档。
`required_tools` 只表示额外依赖，装配时检查是否包含在所选工具声明中；正文规则不变成 Runtime 条件判断。

## VLM 与启动

本机 `config.json` 的路径字段相对于该文件：

```json
{
  "vlm": {"provider": "deepseek", "model": "deepseek-flash", "api_key_env": "DEEPSEEK_API_KEY"},
  "platforms": "..",
  "assembly": {"platform": "robocasa", "profile": "open-drawer", "options": {"new_episode": false}},
  "workspace": "runs/open-drawer"
}
```

GPT 选择 `provider: gpt`、账号可用模型和 `OPENAI_API_KEY`；`api_key` 可保存在被忽略的本机配置，环境变量优先。
`base_url`、`timeout`、`max_tokens` 可配置。GPT 使用 Responses 协议，见 [模型接口](deepseek.md)。
密钥不进入装配目录响应或调用记录。

```bash
uv pip install --python .venv/bin/python -e '.[gui,vlm,test]'
# 首次配置时复制示例，再在本机设置对应 API Key：
cp configs/deepseek-robocasa.example.json workspace/robocasa/config.json
chmod 600 workspace/robocasa/config.json
bash scripts/start-desktop.sh workspace/robocasa/config.json
```

已有本机配置时不要重复覆盖。脚本接受配置路径，并沿用 `PRIMITIVE_CONFIG`；未指定时读取根目录的 `config.json`。
当前 RoboCasa profiles 为 `open-drawer`、`prepare-coffee`、`navigate-kitchen`。
复用已运行过的仿真工作区需显式 `new_episode: true`；旧 Session 不会重放到新 episode，未确认停止的调用仍阻止重建。

`GET /api/assembly` 返回平台/profile 目录和当前模型名称，沿用桌面令牌认证，不提供运行时切换。
关闭按 Agent 停止确认 → 平台退出进行。进程崩溃、断线和取消请求都不能证明物理停止；资源占用仍保留。
框架不自动杀进程、重启平台或重放动作。当前只有本机 stdio 连接，未实现网络连接或跨进程共享控制权仲裁。
