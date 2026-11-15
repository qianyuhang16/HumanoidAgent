# 独立模型配置与平台内部装配

模型配置和平台启动配置分别加载，二者没有相互引用。
`vlm/config.py` 只负责模型连接；`application.create_workbench` 在应用入口组合模型与已连接平台。
平台可以放在任意位置。宿主不扫描 workspace、不读取平台内部文件、不维护平台或工具清单。

## 配置与启动

`configs/vlm.json` 只包含模型设置：

```json
{
  "provider": "deepseek",
  "model": "deepseek-flash",
  "api_key_env": "DEEPSEEK_API_KEY"
}
```

可选 api_key、base_url、timeout、max_tokens。环境变量优先于文件中的密钥。
GPT 示例在 `configs/vlm.gpt.example.json`，填写账号可用模型；模型接口见 [VLM 文档](deepseek.md)。
模型文件不接受平台、profile、Skill 或运行目录字段。

`configs/runtime.json` 只决定启动哪个平台程序、选择哪个组合包，以及运行数据存在哪里：

```json
{
  "connection": {
    "command": "../../.venv/bin/python",
    "args": ["-m", "runtime.server"],
    "cwd": "../workspace/robocasa"
  },
  "profile": "open-drawer",
  "run_dir": "../workspace/robocasa/runs/open-drawer",
  "options": {"new_episode": false}
}
```

cwd 和 run_dir 相对于运行配置文件，均可使用绝对路径；含路径的 command 相对于 cwd，裸命令按 PATH 查找。
args 直接作为参数数组传递，不经过 shell。connection.env 可声明启动进程所需环境变量。
解释器和启动命令用于启动平台进程；启动后的 SDK 路径、环境参数与工具组合由平台内部读取。
运行配置不包含 VLM 字段，也不引用模型配置文件。

```bash
uv pip install --python .venv/bin/python -e '.[gui,vlm,test]'
# 首次配置时执行；已有本机配置不要覆盖。
cp configs/vlm.deepseek.example.json configs/vlm.json
cp configs/runtime.robocasa.example.json configs/runtime.json
chmod 600 configs/vlm.json configs/runtime.json
# 在本机设置 DEEPSEEK_API_KEY 后启动：
bash scripts/start-desktop.sh
# 也可独立指定两份文件：
bash scripts/start-desktop.sh /path/to/vlm.json /path/to/runtime.json
```

脚本及应用入口分别接受 PRIMITIVE_VLM_CONFIG、PRIMITIVE_RUNTIME_CONFIG，默认使用上述两份本机文件。
Python 调用方式为 `create_workbench(vlm_config_path, runtime_config_path)`。
更换模型仅换第一份文件；更换平台或组合包仅换第二份文件。当前在启动时选择，不支持热切换。
RoboCasa 接入包与运行示例位于 `feat/robocasa`；通用模型示例适用于任意平台。

## 平台包结构

下面的 robocasa/ 可以放在项目内、独立仓库或其他本地目录。平台包的约定与存放位置无关：

```text
robocasa/
├── platform.json              # 平台身份、内部环境与默认参数
├── profiles/*.json            # 组合包：环境参数 + Skill 选择
├── skills/<域>/<工具>/skill.md # 全部工具声明、实现入口与使用知识
├── runtime/                   # 本平台的启动入口与能力实现
├── requirements.txt           # 依赖版本及安装入口
├── README.md                  # 平台安装与使用说明
├── assets/                    # 本地资产，Git 忽略
├── deps/                      # 本地上游依赖检出，Git 忽略
└── runs/                      # 可选运行数据位置，Git 忽略
```

接口约定是 platform.json、profiles/、skills/。runtime/ 是当前 Python 平台的实现组织；
其他实现可以使用自己的代码布局和启动命令。assets/、deps/、runs/ 按平台需要存在，不由宿主扫描。
平台目录不保存 VLM 配置。本文没有规定平台必须位于 workspace/<名称>/，也没有目录名到平台 ID 的硬编码映射。
项目内 workspace/ 只是放置当前接入包的便利位置，见 [目录约定](../workspace/README.md)。

## 平台内部读取

RoboCasa 的 runtime/server.py 根据自身文件位置确定平台根目录，调用 tools/assembly.py 中的 PlatformPackage：

1. 读取本包 platform.json，获得平台身份、环境声明及默认参数。
2. 读取本包 profiles/*.json，按请求的 profile ID 选择组合包。
3. 读取该组合包选中的 skills/ 文档，检查工具声明依赖。
4. 初始化 SDK 和共享环境，交给通用进程服务按 Skill 声明注册实现。
5. 向宿主返回实际工具契约、完整 Skill Markdown、平台/profile 元数据及可选视角。

宿主注册远程工具，并保存平台返回的 Skill 文本快照供 Planner 阅读；无需访问平台的源文件路径。
工具与 Markdown 来自同一次装配，修改平台文件在下次启动后生效。
`GET /api/assembly` 返回当前连接平台报告的目录和所选 profile；模型名称由 `/api/status` 提供。

platform.json 示例：

```json
{
  "id": "robocasa",
  "label": "RoboCasa",
  "environment": {
    "python_paths": ["deps/RPent"],
    "variables": {"MUJOCO_GL": "egl"}
  },
  "options": {"assets_path": "assets", "gpu": 0, "image_size": 512}
}
```

RoboCasa 在进程内部应用 variables，将相对于平台根目录的 python_paths 加入导入路径；
其 assets_path 同样相对于平台根目录，与宿主工作目录无关。

profiles/open-drawer.json 示例：

```json
{
  "id": "open-drawer",
  "label": "开抽屉",
  "environment": {"task": "OpenDrawer", "seed": 1},
  "skills": ["skills"]
}
```

组合包描述可用能力集合，没有动作列表。仿真组合包可以选场景，真机组合包可以选设备参数和技能。
参数合并为平台 options → 宿主运行 options → 组合包 environment；运行 options 不允许覆盖组合包已声明字段。
Skill 路径相对于平台根目录；目录递归扫描 skill.md，文件路径只加载对应文档。
当前 RoboCasa 开抽屉与咖啡装配全部 12 个工具，导航装配 6 个。

## 每工具 Skill 与注册

每个工具有自己的 skill.md：YAML 声明 id、版本、描述、domain、Schema、资源、超时、entrypoint；
正文描述适用条件、准备、调用、验证和恢复。完整格式见 [Skill 规范](../skills/README.md)。
工具名称就是 Skill ID；entrypoint 为 Python module:factory，在平台进程的环境中导入。
`tools/registry/loading.py` 调用 factory(context, spec, options) 后注册处理器。
平台代码不另写工具名称清单或注册函数。新增能力需要实现代码和对应 Skill，不修改 Agent 主循环。
required_tools 仅表示额外声明依赖，Skill 正文中的业务条件由 Planner 判断。

## 连接与停止

`runtime/connections` 管理本地 stdio 进程、工具请求、进度、取消和视角传输。
工具调用仍经过 Router、Execution 资源互斥及 Hook；低帧率预览不逐帧写入 Memory。
平台内部装配失败时不会初始化机器人；实现导入失败时关闭已初始化环境。
关闭按 Agent 停止确认 → 平台退出执行。断线、异常和取消请求不确认物理停止。
不自动杀进程、重启或重放动作；协议见 [进程接口](platform-protocol.md)。
