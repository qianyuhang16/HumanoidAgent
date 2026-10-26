# 接入工具、模型与机器人

能力包是可导入的 Python 模块；安装包之后，在配置中填写 `module:factory`。构造器统一接收 `options: dict` 和 `PluginContext`。后者提供 workspace、配置目录、事件日志，以及可选的测试 transport。工具作者自行管理实际机器人和模型连接。

本机专用适配放在独立的 `extensions/local/<name>/` 包中，连同配置、依赖和测试一起维护；新扩展默认忽略，现有 Cobotmagic 已显式纳入版本管理。安装和目录约定见 [本地扩展](../extensions/README.md)。

## 单个 Python 工具

以下例子只演示接口，不包含定位算法：

```python
from primitive_vla.tools import FunctionTool, ToolDefinition, ToolResult

def create_locator(options, context):
    # 在这里校验 options 并连接真实模型。
    client = build_your_localizer(options)

    async def locate(args, call):
        result = await client.locate(args["query"], args["image"])
        return ToolResult(data=result)

    tool = FunctionTool(
        ToolDefinition(
            name="perception.locate",
            description="在指定图像中定位目标。返回二维框，不能直接作为机器人抓取坐标。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "image": {"type": "string"},
                },
                "required": ["query", "image"],
                "additionalProperties": False,
            },
            timeout_s=20,
        ),
        locate,
    )
    tool.close = client.close
    return tool
```

注册方式：

```yaml
tool_providers: []
tools:
  perception.locate:
    adapter: my_package.localizer:create_locator
    options: {}
```

工厂返回 Tool 或 FunctionTool。可选的 `output_schema` 校验 ToolResult.data；schema 使用 JSON Schema Draft 2020-12，引用须自包含，不会自动下载远端 schema。

完整 provider 可以在构造后暴露 `self.tools = [...]`，用 start/close 管理共享资源。以异步非阻塞方式实现调用，耗时阻塞代码放进受管理的 worker。资源只在任务生命周期内初始化，不要在模块 import 时启动机器人。

## 动作工具

动作工具设置 `read_only=False`，声明实际占用的资源，并提供 `async stop(call_id, reason) -> bool`。FunctionTool 第三个参数接收 stop 函数。

调用必须在实际派发前执行 `call.check_active()`。远程驱动还必须在最终派发位置拒绝已停止的 call ID；仅在模型开始推理时检查不足以阻止迟到动作。

`stop` 返回 True 表示已停止/安全交接，不能只表示“接受取消请求”。正常 call 返回也必须表明控制已经结束；若无法确认，设置 `control_released=False`。运行时会尝试停止，失败则锁定控制。

机器人坐标、关节布局、夹爪语义、运动范围、模型入口条件等由该工具声明和检查。核心不会假定两只手臂或七个关节。工具完成后返回实际状态和观测，避免把“控制器执行结束”写成“抓取成功”。

需要保留目标判断的动作可声明 `review_outcome=True`。工具返回 `before_images` 与 `images`，使用一致的相机名表示执行前后。默认由主 Agent 在正常下一轮判断；显式配置 independent 模式及 reviewer 后才另发验证请求。判断按调用 ID 保存来源和修订，默认不增加语义门槛；可配置目标采纳条件。详见[完整接口](verification.md)。

## 远程工具协议

HTTPTools 可用于远程定位、世界模型、VLA 服务或自定义机器人网关。它不是自动生成的 ROS 驱动；服务端负责连接自己的 ROS 版本和消息类型。

```yaml
tool_providers:
  - adapter: primitive_vla.tools.http:HTTPTools
    options:
      endpoint: http://127.0.0.1:9100
      tools:
        - name: world.assess
          description: 基于实际观测评价条件；无法确认时返回 unknown。预测结果须明确标为 prediction。
          parameters:
            type: object
            properties:
              instruction: {type: string}
              image: {type: string}
            required: [instruction, image]
            additionalProperties: false
          read_only: true
          timeout_s: 30
```

服务端 `POST /call` 接收：

```json
{"call_id":"unique-id","tool":"world.assess","arguments":{"instruction":"检查目标","image":"frame-reference"},"deadline":1800000000.0}
```

deadline 是 Unix 秒。返回 ToolResult 的 JSON：

```json
{"status":"ok","data":{"phase":"observation","verdict":"unknown"},"text":"图像无法确认","images":{},"control_released":true}
```

status 可为 ok/error/rejected/timed_out/cancelled。`images` 是来源到 workspace/media 下文件名或 HTTP(S) URL 的映射；远程服务不能返回仅在它自己机器上存在的路径。图像可由上传接口存入 workspace/media。

动作工具的 `POST /stop` 接收 call_id 与 reason，必须返回相同 call_id 以及 `acknowledged: true` 才算确认。服务端应记住已取消 ID，即使 stop 先于迟到的 call 到达，也不得执行该动作。不自动重试 /call。

## 接入决策模型

实现 `step(context, tools)`，返回 `ModelReply(calls=[ToolCall(...)])` 或 `ModelReply(final=FinalAnswer(...))`。参数类型见 agent/contracts.py。模型 Adapter 每个任务新建，允许保存供应商的消息续接状态，并在 close 中释放连接。

内置 JSONModel 使用 HTTP JSON-chat 或 Codex CLI；它将工具定义放入上下文，输出经运行时验证。原生 function calling、其他多模态协议可以通过自定义 Model Adapter 接入，无需修改 Agent。

context.observations 提供近期原始观测的紧凑索引；context.state 是可选判断视图。
模型可以按反馈直接选择下一次调用，无需先提交 claim。`state.query` 默认查询判断，
指定 `kind="observations"` 后按 subject/sensor 发现观测；property 过滤仅用于判断。
`state.read` 按 ID 分页读取完整记录。索引不发送大数组，历史截断不删除原始记录。

## 可选结束检查

配置 finish_check 为一个提供 `async check(answer, context)` 的 Adapter。返回 None 接受，返回理由字符串拒绝并让模型继续处理。它与其他插件相同，接受 options/context，支持 start/close。

不配置时 completed 是决策模型根据反馈作出的结论。自定义 benchmark 可用结束检查强制独立验收，不需要把 benchmark 谓词加入核心。

## 验证自己的扩展

先用 fixture 或假服务器检查参数、输出、未知结果、取消和停止确认。动作工具还应确认无效输入零派发、同一调用不重复执行、迟到结果不能恢复控制。随后使用实际场景评估模型选择与物理效果。新增工具的测试允许多种合理调用顺序。

## 提供结构化状态

```python
from primitive_vla.state.contracts import Observation, StateClaim

result = ToolResult(
    observations=[Observation(
        id="frame-42", sensor="robot.front", captured_at=actual_capture_unix_seconds,
        artifacts={"image": "frame-42.jpg"})],
    claims=[StateClaim(
        subject="cup-1", property="holding.v1", value=True,
        basis="inferred", evidence=["frame-42"])],
    images={"front": "frame-42.jpg"},
)
```

时间来自真实采集源；缺失时留 None。相同观测可跨调用复用，ID 对应内容不可改写。
对象、坐标系和单位由 adapter 明确；缺少语义时返回 unknown，不靠补字段猜测。
内置 holding.v1 为布尔值；position.v1 要求 coordinates/frame/unit；detection.v1 要求
label/frame/unit 与 box_xyxy 或 pixel_uv；relation.v1 要求 relation/object；progress.v1
要求 value/metric。未知值用 status=unknown、value=None；known 必须显式提供值和依据，
值为 null 时属性 schema 必须允许。partial 表示部分信息，仍需符合属性契约。

单独状态后端实现 estimate(StateRequest, CallContext) -> StateResult。请求只包含 subject、
property 与已读取的证据记录，并提供 request.artifacts 类型化资源视图；可返回 claims、
artifacts 或任意 JSON data，不获得状态修改入口。构造器仍为 options/context。
按配置声明支持范围后由 state.estimate 暴露，不需要增加新的 Agent 循环。

图片、点云、深度等输入输出共享 Artifact 契约；可选字段及资源槽可为 null。
导航位姿、触觉、本体反馈等保留在 Observation.data，subject/frame/metadata 可省略或为 null。
需要语义判断时按 state.schemas 注册领域属性，避免强行转换为内置三维 position.v1。
地图与实时位姿应分开记录：地图可标 lifetime="reference"，位姿保持默认 scene。
完整格式、输入 schema 和后端示例见 [多模态契约](multimodal-contracts.md)。

## 可查询的动作后端

新动作后端可实现 ExecutionBackend.start(arguments, context)、inspect(call_id, context)、
stop(call_id, reason)，返回 ExecutionUpdate。用 execution.tool.ExecutionTool 包装为 Tool。
后端必须能按 call_id 查询和停止，包括 start 回复丢失的情况；内部 handle 可由后端维护。

phase 区分 accepted/running/finished/stopped/unknown，control 区分 held/released/unknown。
只有明确停止／结束且控制已释放才可交接。可选 ended_at 必须是真实结束的 Unix 秒，
用于判断随后采集、但早于响应到达的图像是否满足严格验收。旧 call/stop 继续支持；若同时填写新旧字段，
execution.control 为准。远端取消 fencing 仍是后端责任。

直接调用 ExecutionTool.call 会轮询到终态；通过下述 execution.submit 提交时，轮询发生在后台任务中。
长导航或全身运动可切为后端支持的有界动作段，段间确认控制交接，再交给模型决定下一步。
段内避障、平衡和闭环控制由后端承担；不能提前报告 released 来伪装后台执行。
对于四足／人形，安全停止可能是交接给站立或平衡控制器，不能假设停止等于关闭电机。
Agent 可取消自己的后台任务；运行中用户追加目标、设备级故障隔离和联合任务仍见 [TODO](../TODO.md)。

后台调用 StateCapabilities.estimate 返回结果和尝试记录；调用方通过 StateJournal.record_claims
明确采纳并指定真实来源。模型提供判断与系统修改记录的权限分开，不以 Skill 代替运行检查。

## 一个 Agent 的异步设备动作

动作的 ToolDefinition 增加可选 device_id，其他 call/stop 或 start/inspect/stop 协议不变：

```python
definition = ToolDefinition(
    name="robot_a.navigate", description="导航到指定站点，返回实际执行结果。",
    device_id="robot-a", read_only=False, resources=["robot-a:base"],
    parameters={"type": "object", "properties": {"target": {"type": "string"}},
                "required": ["target"], "additionalProperties": False},
    timeout_s=120,
)
```

同一物理机器人上的动作声明相同 device_id，即使一个控制底盘、另一个控制手臂，也会
串行执行。必须协调的全身动作由单个后端实现。跨机器人共享的工位可使用相同 resources
字符串；内部还自动占用 `device:<device_id>`，该命名用于设备控制，不要另作其他用途。
device_id 不由模型在提交时填写。省略／null 的旧动作只能通过原同步接口调用。

存在可异步执行的动作时，Runtime 自动装配以下工具：

| 工具 | 输入与行为 |
| --- | --- |
| execution.submit | tool、arguments：校验目标工具后入队，返回 task 与 revision |
| execution.query | 可选 task_id 或 device_id：查询任务及参数；task_id 优先 |
| execution.wait | after、可选 timeout_s：等待 revision 变化，最多 10 秒；超时不取消动作 |
| execution.cancel | task_id：排队任务撤销；运行任务请求停止并等待确认 |

例如已有 robot_a.navigate 与 robot_b.pick 两个真实工具时，模型可以同时提交：

```json
{"calls": [
  {"name": "execution.submit", "arguments": {"tool": "robot_a.navigate", "arguments": {"target": "station-2"}}},
  {"name": "execution.submit", "arguments": {"tool": "robot_b.pick", "arguments": {"object": "cup-1"}}}
]}
```

这两个提交工具依次返回，物理动作可在不同设备上重叠执行。arguments 必须匹配原工具
schema；禁止的工具不能借 submit 调用。相同提交 call_id 去重，重新生成 ID 表示新任务。
模型需要前一动作的结果才能决定下一步时，应等待结果而不是预先排入队列；FIFO 不隐含
“前一任务成功后才执行”。配置的动作前提会在真正派发时重新检查。

任务视图包含 task_id、device_id、tool、status、result_ref、execution_phase、control。
status 的 queued/running/cancelling 是调度状态；running 包括已安排派发但后端尚未确认。
终态为 finished/error/rejected/timed_out/cancelled/unknown，finished 仅表示调用正常结束。
execution_phase 表达最终控制器阶段，control 表达最终控制确认；进行中可能为 null。
提交成功不代表控制已释放或目标完成，不能将提交回执当作物理证据。

context.executions 保留所有活动任务和有限的近期结束任务，execution_revision 可用于
下一次 wait。完成后，实际动作结果以 task_id 作为 call_id 写入会话／状态；result_ref
指向该记录，使用 state.read 分页读取完整结果，主 Agent 或原 reviewer 可继续判断。
不要用提交调用的 ID 代替实际动作 ID 做动作验收。

动作期限取会话剩余时间与实际派发时开始计算的工具 timeout_s 的较早者。等待不会延长
会话期限。存在未完成任务时不能结束为 completed；blocked/failed、预算耗尽或取消会
撤销队列并停止活动动作。停止未确认会保留全局控制锁，不能继续提交其他设备动作。

快照与任务事件保留队列记录，但服务重启后不自动恢复执行。旧任务标为历史记录，仍需
核对机器人侧实际控制状态。本轮不提供优先级重排、依赖图、联合启动／整组同步控制。
