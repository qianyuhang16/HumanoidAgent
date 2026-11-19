# 扩展接入契约

## Planning 适配器

实现 `async decide(context: PlanningContext) -> Decision`。适配器将 `context.data` 和
`context.action_schema` 转为所用 VLM/LLM 的输入，并将模型输出转换成 `Decision(action, arguments)`。
通用 `planning.ModelPlanner` 接收独立的 `vlm.VLM` 实现；`vlm.deepseek.DeepSeekVLM` 只负责通信，见
[DeepSeek 桌面配置](deepseek.md)。模型请求须支持 asyncio 取消，以及时暂停或中止一次推理。

| 框架动作 | 参数及用途 |
| --- | --- |
| `discover_tools` | `query, offset, limit`；按当前机器人类型和授权过滤能力摘要 |
| `inspect_tool` | `name`；获取模型可读的工具定义和同名 Skill 首页，包括输入 Schema 及使用语义 |
| `call_tool` | `name, arguments`；调用外部能力 |
| `tool_status / cancel_tool` | `call_id`；查询或请求停止当前 Session 的调用 |
| `list_skills / read_skill` | Skill 列表分页；读取时提供 `id, offset, limit` |
| `memory_search` | `query, filters, offset, limit`；不区分大小写的逐词 AND 字面匹配，空查询按条件列出记录；空结果提示缩短关键词 |
| `memory_get` | `id, offset, limit`；读取 Markdown 工作记忆分页 |
| `memory_index` | `offset, limit`；读取长期 MEMORY.md 索引 |
| `memory_write` | `title, content, lifetime, source_ids, tags`；写 Markdown Planning note |
| `memory_update` | `id, changes`；更新 Planning note，不修改 Tool 证据 |
| `memory_delete` | `id`；删除当前 Session 的短期工作文档，不删除原始日志 |
| `evidence_get` | `id, version?, offset, limit`；读取原始事件或整理时引用的来源版本，同时附上记录中的图片 |
| `subscribe` | `sources`；替换当前任务关注的持续观测源 |
| `wait` | 可选 `seconds`；等事件或周期检查，受剩余时间预算限制 |
| `ask_user` | `question, resource_ids`；提问并展示图像/地图等资源 |
| `finish` | `message, outcome, evidence_ids`；提交任务报告，outcome 为 success 或 failed，evidence_ids 使用 journal 记录 ID 或图片短引用 |

直接读取 Skill 或 Memory 不需要伪装为机器人 Tool。它们是 Planner 可用的框架操作。
Tool 返回的本地图像自动进入下一轮模型输入，不需要模型再调用资源读取动作。
通用 ModelPlanner 按资源引用读取图像；由宿主注入 `Memory.read_resource`。
VLM 实现只接收字节与媒体类型，不知道 Memory 目录或 Planning 提示词。
跨 Session 的长期知识共享属于同一受信任应用，V1 不提供多租户隔离。

`evidence_get.id`、`memory_write.source_ids`、`finish.evidence_ids` 使用记录 ID，例如
`records[].id` 或 `evidence_id`。`ask_user.resource_ids` 使用资源 ID；`call_id` 标识执行调用。
图片短引用（如 image_1）可用于以上证据/资源字段，由宿主解析；其他三类 ID 不可互换。

图片使用既有 ResourceRef 承载：metadata 中 camera、width、height 可供模型查看，其他元数据留在内部。
纯图片 Tool 返回空 payload 和图片 resource_refs 即可，模型收到图片及简短标签。
需要消费图片的 Tool 在顶层字符串参数声明 `format: image-ref`；例如 `image: {type: string, format: image-ref}`。
模型传 image_1，Runtime 解析成真实资源 ID 后交给 Provider；其他参数原样传递。
平台负责读取资源并校验图像是否属于当前场景、是否过期，不要求模型传递内部版本字段。
短引用仅在所属 Session 内有效，重排及重载不重新编号。跨 Session 使用原始证据记录重新加载图片。

## Tool 文件与注册

每个工具对应一个 skill.md，包含名称（id）、版本、描述和 tool 元信息。
元信息定义 domain、input_schema、output_schema、provider、supported_robots、required_resources、execution_mode、timeout，
以及 entrypoint 与 options；具体格式见 [Skill 规范](../skills/README.md)。

运行配置选择文件或目录后，框架读取同一份元信息生成 ToolSpec 与 Skill 目录。
子进程通用加载器按 entrypoint 导入工厂，调用 factory(context, spec, options)，取得 ToolHandler 并注册。
平台初始化只创建共享环境和资源，不再用 Python 列表枚举要注册的工具。

名称不决定工具域，例如 vision.compare_images 可以在文档中声明为 State。
supported_robots 是本体类型，robot_id 是控制资源对应的具体实例；只有 Execution 可声明运动资源。
Tool 的调用接口与 Skill 的使用知识仍有不同职责，但由同一文件装配，不再通过另一份索引维持关系。
新增工具文件即可扩展目录；真正新增算法时，在文档的 entrypoint 指向的模块实现处理器。

## ToolHandler 生命周期

平台 ToolHandler 的两个方法（位于 tools/contracts.py）：

```python
async def execute(arguments: dict, context: CallContext) -> ToolResult: ...
async def cancel(context: CallContext) -> bool: ...
```

`context` 包含 `call_id / session_id / task_id / robot_id`，以及 `cancel_requested` 事件和
`await context.progress(payload, resource_refs)`。异步 Provider 的 execute 应覆盖完整操作生命周期，
不能仅提交远程作业后立刻返回；需要在适配层等待远程终态，进度通过回调写入 Memory。

`cancel` 是幂等的停止确认协议。返回 `True` 必须证明物理操作已经停止；`False` 表示还不能确认。
仅取消客户端 HTTP 请求或 Python Task 不满足契约。通信异常也不能证明已经停止。
重启恢复时只保留 `call_id` 和关联标识，不会重建旧 Python 任务；Provider 应用该标识找到远程作业。

正常结果为 `ToolResult(payload, resource_refs, verification)`。`output_schema` 校验业务 payload，
资源引用和验证结构属于统一结果封装。原始图像、地图、视频通过资源引用返回；畸变校正、裁剪、
缩放、坐标适配等由 Provider 或其适配组件完成。
`resource_refs` 中的 PNG、JPEG、WebP、GIF 本地图像及 Verification 的图像证据会自动附给模型；
Provider 须先把可展示的图像字节存入 blob，外部 URI、深度数组、地图等引用不会被当作图片读取。
跨进程接入由平台协议传输资源字节；资源 ID 用于关联证据，不是模型必须额外读取的入口。

Verification Tool 必须提供：

```python
Verification(
    subject=verification_target,
    conclusion="uncertain",   # success / failure / uncertain
    reason=reason_or_basis,
    evidence=resource_references,
    related_call_id=action_call_id,
    failure_type=None,
)
```

`reason` 应说明判断依据；不确定时说明原因。可以没有图像证据，例如几何/接触传感器验证，
此时 payload 保存可复查的测量值。Execution 返回 Verification 结论会被拒绝。

可预期业务错误抛出 `ToolError(Failure, message, evidence)`，失败类型包括接口、Skill 条件、
资源冲突、权限、执行异常、超时、效果失败及状态不确定。普通 Provider 异常被归类为执行错误。
Verification 的效果失败或不确定通常应作为正常验证结果返回，不用异常替代结论。

## Hook 与持续观测

通过 `agent.hooks.add(domain, async_hook)` 注册 `async hook(stage, record)`。
stage 为 `before_call / after_call / on_error / on_progress`。创建 ID、时间戳、Memory 记录、
基础权限和资源互斥已经由框架处理；Hook 只补充扩展行为。

持续观测可使用：

```python
await agent.observations.publish(
    session_id=session.id,
    task_id=session.task_id,
    source=source_name,
    payload=observation,
    resource_refs=references,
    significant=should_wake_planning,
    ttl=60,
)
```

摄像头、SLAM 等流由应用/Provider 启停，Runtime 不建立传感器线程。所有框架调用在同一个
asyncio 循环内；SDK 的阻塞工作应由适配层放入线程、进程或远程服务，再把结果送回该循环。

## 用户交互

`ask_user` 暂停决策到 `waiting_user`。宿主展示 `session.question` 和 `last_result.resources`。
地图坐标系、点位、对象选择都作为 `service.respond(session_id, answer, payload)` 的业务数据写入 Memory，
无需核心定义固定 `A/B/holding_object` 字段。实际网页、地图渲染和传感器 UI 由宿主提供。

工作区平台通过 command 接入的完整格式见 [装配说明](assembly.md) 与 [进程协议](platform-protocol.md)。
