# 本地平台进程协议 v2

Agent 核心不包含机器人接入实现。接入程序可以使用任意环境；使用 Python 时可复用
`primitive_vla.runtime.connections.server.serve(factory)`。这个通用模块处理请求关联、进度、取消和视角传输，
平台模块实现初始化、ToolHandler 和清理，不依赖具体 VLM。

## 传输

通过 stdin/stdout 传递每行一个 UTF-8 JSON 对象。请求：
`{"id":"uuid","method":"execute","params":{...}}`。
响应含相同 id，以及 result 或 error；error 含 kind、message、evidence，与 ToolError 一致。
请求可以并发，execute 的最终响应必须等待操作结束，期间可继续处理 cancel 和状态请求。
stdout 不写日志；Python 宿主将 stdout 文件描述符重定向到 stderr，另保留协议输出管道。

| 方法 | 参数 | 返回 |
| --- | --- | --- |
| initialize | profile_id、options、绝对 workdir、unfinished_calls | protocol=2、robot、resources、aliases、tools、skills、platform、profile_id、views |
| execute | name、arguments、context | ToolResult：payload、resource_refs、verification |
| cancel | call_id | true 仅表示已确认物理停止；false 表示未确认 |
| view_open | subscription、view_id | 订阅建立确认 |
| view_close | subscription | 退订和采集清理确认 |
| shutdown | 空对象 | 平台清理完成确认，随后进程退出 |

context 包含 call_id、session_id、task_id、robot_id。工具 Schema 与调用参数在 Agent 的注册/Router 入口校验，
平台实现不重复执行同一校验。SDK 自身语义和状态要求仍由平台处理。
启动收到的 unfinished_calls 是宿主持久化的未结束调用，不能丢弃后假装没有动作；
RoboCasa 在创建任何模拟器前拒绝该机器人仍有未结束调用的启动。

异步事件与请求共用输出管道：

- progress：event、id（execute 请求 ID）、payload、resource_refs。
- view：event、id（subscription）、frame；frame.data 为 Base64，其余字段遵循 ViewFrame。
- view_error：event、id（subscription）、message。预览失败不改变控制动作结果。

## 资源与状态

工具资源和验证证据仍使用 ResourceRef。传输 uri 为 `blob:<sha256>`，字节位于 `workdir/transfer/<sha256>`。
Agent 验证引用并将字节导入自己的资源存储，保留资源 ID 与证据关联。预览使用有界最新帧队列，不逐帧写文件或 Memory。
当前传输目录保留本次运行资源；未增加资源垃圾回收或网络文件协议。

RoboCasa 子进程使用自己的 `native/platform-state` 保存 episode 关联和原始资源；Agent Memory 由宿主单独写入，
不让两个进程并发修改同一索引。平台元数据属于接入实现，不改变 Agent 的 Memory 契约。

## Python 接入

factory 是异步上下文管理器，接收 initialize 参数，产出 PlatformRuntime：
机器人信息、资源名称/别名、资源字节读取函数、SkillStore、平台目录元数据、所选 profile_id、可选 RobotViewSource 和工具共享的 context。
平台自行读取内部 platform.json、profiles/、skills/；通用协议模块持有 ToolRegistry，并按平台提供的 SkillStore 装配。
退出时必须关闭 SDK；仍有控制未确认时拒绝关闭。
实际例子见 [RoboCasa 启动入口](../workspace/robocasa/runtime/server.py)，可直接参照其结构实现其他平台。

工具声明来自平台内部所选 skill.md，每项包含 spec、entrypoint、options。spec 的名称取自 Skill ID，描述取自同一文档。
`tools/registry/loading.py` 在平台环境中导入 module:factory，调用 factory(context, spec, options) 后注册返回的处理器。
ToolHandler 定义位于 `tools/contracts.py`，只有 execute 和 cancel 两个方法。
框架中的 ToolSpec.provider 仅是“实现来源”的描述标识，不再对应内置 providers 目录或注册入口。

初始化响应的 skills 是完整 Markdown 字符串列表，宿主保存文本快照并分页供 Planner 阅读，不访问平台文件路径。
platform 包含 id、label、profiles（各组合包的 id/label）；这些信息由平台报告。
模型配置不属于进程协议。v2 不接受由宿主发送工具声明的旧初始化格式。

## 停止与异常

Tool 返回结果表示该次底层操作已结束，不表示业务目标达成。Verification 结论独立返回。
execute 报错、管道断开、子进程退出均不确认物理停止；Router 保留 Execution 占用，等待 cancel 明确确认。
接入程序处理异常必须保留失败类型和证据，不能自动重试物理动作。
shutdown 在未排空调用或仍未确认停止时拒绝，不能绕过 cancel 直接结束进程。
当前实现不自动重启进程，不通过 kill 伪装停止成功。真机恢复需要平台自己实现可识别持久 call_id 的停止协议。
