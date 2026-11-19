# 每个工具一个 skill.md

工具声明与使用说明放在同一个文件：`skills/<domain>/<tool>/skill.md`。
文件的 YAML 元信息定义可调用契约和执行入口；Markdown 正文说明适用条件、准备、验证和恢复。
平台内部读取 profiles/ 组合包，按选择的文件或目录加载 Skill 并注册工具，不维护 Python 工具名称清单。
宿主接收平台返回的工具契约与 Markdown 文本快照，不读取平台的文件路径。

## 面向模型的最小接口

参数只表达模型必须做出的选择，例如目标物体、目的地、图片引用或像素位置。
相机、帧和场景之间的关联由系统维护，不要求模型抄写内部 UUID、时间戳、步号或路径。
普通读图只返回图片及相机/描述、尺寸和短引用；需要像素定位时才传 `image`、`row`、`column`。
需要消费图片的顶层字符串参数声明 `format: image-ref`，Runtime 解析短引用，平台检查帧是否有效。

正文说明能力、必要条件、参数含义、结果含义和失败后可选的处理方法。
不重复罗列注册 Schema 或部署配置，不增加模型无需参与的准备动作，不写固定任务流程。
简单工具可用几句话说明，不为凑齐标题重复同一内容。
精简结果必须保留事实：Execution 的完成状态、错误和 Verification 结论不能因附带图片而消失。

以下是平台提供方编写相机工具的格式示例，entrypoint 必须替换为实际实现，并非内置工具：

```markdown
---
id: state.camera
version: "1.0"
description: 获取前视相机图片。
tool:
  domain: state
  provider: your-platform
  input_schema:
    type: object
    properties: {}
    additionalProperties: false
  output_schema:
    type: object
  execution_mode: sync
  timeout: 30
  entrypoint: robot_tools:Camera
---
# 获取前视图像

无需参数。返回图片、相机名称、尺寸和图片引用。
需要观察当前环境时调用；动作后需要新画面时重新采集。
图像不清晰或目标被遮挡时，可调整视角或使用其他观察能力。
```

纯图片结果使用空业务 payload 与图片 resource_refs；实际像素由模型适配层附加。
图片定位等工具应单独声明 image-ref 参数，不让相机工具承担任务规划。

## 字段与装配

- `id` 同时是 Skill ID 和 Tool 名称，是名称的唯一来源；在同一次装配中必须唯一。
- `version` 为字符串，`description` 同时用于 Skill 目录和工具发现。
- `tool` 声明工具域、输入/输出 JSON Schema、实现来源、本体支持、控制资源、执行方式和超时。
- `entrypoint` 是平台环境可导入的 `module:factory`。框架调用 `factory(context, spec, options)`，取得实现 execute/cancel 的 ToolHandler。
- `options` 是交给该实现的配置，例如映射到现有 SDK 的某个操作。模块导入和能力执行发生在平台子进程。
- `required_tools` 可选，用于声明额外工具依赖，不表示调用顺序；不能代替本工具的 `tool` 声明。

`SkillStore.load(path)` 接收一个 Markdown 文件，或递归扫描目录中的 `skill.md`。
同一份已解析元信息提供工具注册和 Skill 目录，正文由 VLM 按需分页读取。
工具名称与 Skill ID 相同，因此调用 `read_skill` 时直接使用工具名称即可。
不含 tool 的文档仅作为知识读取，不会注册可执行能力。

修改名称、描述、Schema、资源、超时或入口，只需修改对应 skill.md 并重新启动。
新增工具：添加一个目录和 skill.md，指向已有实现或为新算法提供实现；被选中的目录会自动发现它。
移除文件或取消运行配置选择，该工具就不会注册。目录本身不含任务执行顺序。

## 规则与接口

正文中的技能适用条件和恢复策略由 VLM 判断，不把它们编译为固定工作流。
参数类型与静态范围在 Tool 调用入口校验；当前 episode、实际图像尺寸等动态状态由能力实现检查。
控制资源互斥与停止确认由框架执行。Execution 的 completed 不表示任务目标达成。
平台共享 command 维持设备和仿真生命周期，每个工具的入口由自己的 skill.md 指定，不逐次重启设备。
