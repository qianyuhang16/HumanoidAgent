# 扩展

可复用示例也独立于核心包：[demo](demo/README.md) 提供离线电池场景和 MockRobot，[qwen_vlm](qwen_vlm/README.md) 提供 Qwen3-VL 权重服务。它们有独立依赖和入口，需要显式安装；主包不导入或自动启用它们。

`extensions/local/` 用于具体机器人、场景和模型组合的本地适配，新文件默认由主仓库的 `.gitignore` 忽略。未跟踪的代码保留在磁盘上，不会随主仓库提交或克隆；需要共享和版本管理时，可将扩展作为独立仓库维护。`tabletop` 示例的源码、配置、说明和测试已显式纳入主仓库版本管理；其虚拟环境、密钥、模型权重和运行产物仍被忽略。

核心通过现有 `adapter: module:factory` 加载已安装的 Python 包，不扫描该目录、不自动执行扩展，也不依赖某个扩展的存在。扩展依赖核心的公开工具 Interface，核心不反向导入扩展。

## 目录约定

```text
extensions/
  README.md                     # 通用接入说明，保留在主仓库
  demo/                         # 独立软件演示包，纳入版本管理
  qwen_vlm/                     # 独立 Qwen 权重服务包，纳入版本管理
  local/                        # 默认忽略；tabletop 源码已显式跟踪
    my_robot/
      pyproject.toml            # 独立包、依赖和命令入口
      src/my_robot_tools/       # factory、模型及设备适配
      configs/agent.yaml        # 工具注册与本机参数
      instructions/             # 可选：提供给 Agent 的使用说明
      scripts/                  # 下载、启动或诊断入口
      tests/                    # 扩展自己的测试
      README.md                 # 环境与启动说明
```

仅有使用说明的 skill 可以是文本文件，通过配置的 `instructions` 加入模型上下文。会加载模型、连接机器人或控制仿真的 Python 代码属于工具 Adapter，应放进扩展包。名称和目录不改变它们的执行权限。

## 接入

先把扩展安装到运行 Agent 的 Python 环境：

```bash
python -m pip install -e .
python -m pip install -e extensions/local/my_robot
```

扩展的 `pyproject.toml` 可以使用普通的 src 布局，并声明对 `primitive-vla` 的依赖。GPU、ROS 等专用依赖放在扩展自己的依赖表或 worker 环境中，不加入核心包。

在扩展自己的配置中注册：

```yaml
schema_version: 2
model:
  adapter: primitive_vla.agent.model:JSONModel
  options:
    base_url: http://127.0.0.1:8000/v1
    model: your-model-id
tool_providers:
  - adapter: my_robot_tools.provider:RobotTools
    options: {}
```

然后运行：

```bash
python -m primitive_vla serve \
  --config extensions/local/my_robot/configs/agent.yaml \
  --workspace workspace/my_robot
```

factory 接受 `(options, PluginContext)` 并返回导出 `tools` 的对象；动作停止、资源声明和生命周期约定见 [工具接入](../docs/tools.md)。配置与指令中的相对路径应由所属 Adapter 按配置目录解析。

扩展的子进程如果使用另一个 Python，该环境也需要能导入扩展包。例如，在已有 GPU 环境中以 editable 方式安装扩展，或用 `--system-site-packages` 创建项目内的独立 worker 环境，再安装扩展。不要依赖主进程恰好从哪个目录启动。

## Git 与验证

主仓库保留通用框架、公共 Interface、模板和通用测试。扩展源码、专用测试、配置和脚本放在扩展内。密钥放在 `.env`，权重及执行结果放在 `workspace/`；这些位置也被忽略。本机配置还可使用 `configs/local/` 或 `*.local.yaml` 等文件名。

核心测试不要求安装任何本地扩展：

```bash
python -m unittest discover -s tests -t . -v
```

安装扩展后单独运行它的测试：

```bash
python -m unittest discover -s extensions/local/my_robot/tests -v
```

可用 `git check-ignore -v extensions/local/my_robot/pyproject.toml` 确认忽略规则。`.gitignore` 不会停止跟踪已经提交的文件；这里约定的是新建本地扩展的位置。
