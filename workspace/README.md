# 平台包目录约定

workspace/ 是本仓库存放平台包的便利位置，Agent 不扫描这个目录。平台可整体放在任意位置，
宿主通过独立运行配置中的 command、args、cwd 启动它，通过 profile ID 选择组合包。
VLM 配置单独放在 configs/ 或其他指定位置，不属于任何平台包。

每个平台包包含以下接口文件，均由平台自身入口读取：

| 路径 | 职责 |
| --- | --- |
| platform.json | 平台 ID、名称、内部环境声明及默认参数 |
| profiles/*.json | 组合包 ID、环境参数和要加载的 Skill 文件/目录 |
| skills/**/skill.md | 所有 Skill 说明；每个工具的声明、Schema 和实现入口也在这里 |

当前 Python 平台还采用 runtime/ 存放启动入口与实际能力实现，requirements.txt 固定依赖，README.md 说明安装。
assets/、deps/ 为可选本地内容；运行数据由 run_dir 指定，可放 runs/ 或平台包外。
工具入口代码在平台进程内导入。平台向宿主报告工具、Skill Markdown、机器人资源和视角，宿主不读取平台文件。

同一模型配置可供任意平台使用，同一平台可配任意受支持模型。
具体配置、路径基准与新增平台的方法见 [装配说明](../docs/assembly.md)。
