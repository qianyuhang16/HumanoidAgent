# 独立模型配置与平台内部装配

用户入口见根 README。`vlm/config.py` 只管理模型连接；`application.create_workbench` 在应用入口组合模型与平台。平台可位于任意路径，宿主不扫描 workspace、不读取平台内部文件。

## 两份配置

configs/vlm.json 只包含模型连接，例如：

```json
{"provider":"deepseek","model":"deepseek-flash","api_key_env":"DEEPSEEK_API_KEY"}
```

可选 api_key、base_url、timeout、max_tokens；环境变量优先于文件密钥。GPT 示例在 configs/vlm.gpt.example.json。真实配置被 Git 忽略，平台包不保存 VLM 配置。

configs/runtime.json 只指定平台进程、profile 和数据目录。下面是路径说明用的模板，command 和 profile 要按实际平台填写，仓库不内置这个示例平台：

```json
{
  "connection": {
    "command": "/path/to/platform-python",
    "args": ["-m", "runtime.server"],
    "cwd": "/path/to/platform-package"
  },
  "profile": "your-profile",
  "run_dir": "../workspace/runs/your-profile",
  "options": {}
}
```

cwd 和 run_dir 相对于运行配置文件；含路径的 command 相对于 cwd，裸命令按 PATH 查找。args 直接传递、不经过 shell。connection.env 用于进程环境变量。模型字段不进入运行配置。

```bash
bash scripts/start-desktop.sh configs/vlm.json configs/runtime.json
```

也可通过 PRIMITIVE_VLM_CONFIG、PRIMITIVE_RUNTIME_CONFIG 指定文件。应用入口为 `create_workbench(vlm_config_path, runtime_config_path)`。当前启动时装配，不支持运行中热切换。

RoboCasa 包和可运行配置仅在 [feat/robocasa](https://github.com/qianyuhang16/HumanoidAgent/tree/feat/robocasa)，安装和场景选择见该分支 README。

## 平台内部结构

```text
platform-package/
├── platform.json
├── profiles/*.json
├── skills/<domain>/<tool>/skill.md
├── runtime/                 # 当前 Python 接入的入口与能力实现
├── requirements.txt
├── README.md
├── assets/                  # 本机内容
├── deps/                    # 本机依赖检出
└── runs/                    # 可选运行数据目录
```

接口约定是 platform.json、profiles/、skills/；runtime/ 是当前 Python 实现组织，其他平台可使用自己的代码布局和启动命令。

- platform.json 声明平台 ID、名称、内部 Python 路径、环境变量和默认 options。
- profiles/*.json 声明组合包 ID、environment 和选中的 Skill 文件/目录。组合包提供能力集合，不提供动作顺序。
- skill.md 的 YAML 声明工具契约与 entrypoint；正文提供模型使用知识。完整文档用于装配，模型只读正文与输入参数。

平台入口按以下顺序准备能力：读取自身配置 → 选择 profile → 加载选中的 Skill → 初始化共享环境 → 根据 entrypoint 注册处理器 → 向宿主报告工具、Markdown、机器人资源和可选视角。

`tools/assembly.py` 提供平台内部加载器，`tools/registry/loading.py` 导入处理器，`runtime/connections` 管理进程、通信、停止和视角。宿主只消费平台报告，不到平台磁盘上自行找文件。

参数按平台 options → 宿主 options → profile.environment 合并；宿主 options 不允许覆盖 profile 已声明的字段。Skill 路径相对于平台根目录，目录递归发现 skill.md，required_tools 声明额外依赖，不表示调用顺序。

## 模型接口与生命周期

相机工具直接返回图片；需要图片的参数用 `format: image-ref` 声明，模型只提供稳定引用，平台负责检查其有效性。接口与完整 Skill 规范见 [扩展契约](extensions.md) 和 [Skill 规范](../skills/README.md)。

添加能力需要 Skill 和实现代码，不修改 Agent 主循环。修改工具文件后重启生效；运行记录保留原始契约结果，不自动变成新任务事实。

进程断线、超时、暂停和取消请求不能证明机器人已经停止。关闭遵循 Agent 停止确认 → 平台退出；不自动杀进程、重放动作或重启控制。协议见 [平台协议](platform-protocol.md)。
