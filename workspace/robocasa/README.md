# RoboCasa 平台包

这个目录是一份独立平台接入，不属于 Agent 核心。模型选择与平台代码分离。

- `platform.json`：平台身份、内部 Python 依赖路径、环境变量及默认设置。
- `profiles/`：开抽屉、做咖啡、厨房导航的场景参数与 Skill 文件选择。
- `skills/<域>/<工具>/skill.md`：每个工具一个文件，声明名称、Schema、资源、超时、实现入口及使用知识。
- `runtime/server.py`：自行读取本包的 platform.json、profiles/ 和 skills/，初始化环境后向宿主报告工具、Skill 与相机。
- `runtime/environment.py`：机器人能力适配、停止确认与 episode 生命周期。
- `runtime/backend.py`、`views.py`：原生模拟器线程与相机订阅。
- `requirements.txt`：模拟器依赖，RPent 源码另放 `deps/RPent`。

平台包可以放在任意位置；内部路径相对于本包根目录。`assets/`、`deps/`、`runs/` 为本机内容，Git 忽略。
模型配置单独放在平台包之外。宿主启动配置通过 command/args/cwd 选择解释器和本包入口，并传入 profile、run_dir 和 options。
启动示例见 configs/runtime.robocasa.example.json；解释器环境中需安装框架包和仿真依赖。
完整安装见 [RoboCasa 文档](../../docs/robocasa.md)，装配与启动见 [工作区装配](../../docs/assembly.md)。

开抽屉与咖啡配置装配全部 12 个工具文件，导航配置装配 6 个；没有 Python 工具注册清单。
可从 [observe/skill.md](skills/state/observe/skill.md) 查看一个完整例子。
修改或新增文档并重新启动即可改变注册结果，新算法需在 entrypoint 对应的模块实现。
这些文件提供工具契约和使用知识，不是专家轨迹或经过成功率评测的策略。
RoboCasa 进程重启不恢复物理状态。旧运行目录重启需要显式 new_episode，且不会复用旧任务继续动作。
