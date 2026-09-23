# 接入工具、模型与机器人

能力包是可导入的 Python 模块；安装包之后，在配置中填写 `module:factory`。构造器统一接收 `options: dict` 和 `PluginContext`。后者提供 workspace、配置目录、事件日志，以及可选的测试 transport。工具作者自行管理实际机器人和模型连接。

本机专用适配放在独立的 `extensions/local/<name>/` 包中，连同配置、依赖和测试一起维护；这个目录不进入主仓库。安装和目录约定见 [本地扩展](../extensions/README.md)。

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

## 可选结束检查

配置 finish_check 为一个提供 `async check(answer, context)` 的 Adapter。返回 None 接受，返回理由字符串拒绝并让模型继续处理。它与其他插件相同，接受 options/context，支持 start/close。

不配置时 completed 是决策模型根据反馈作出的结论。自定义 benchmark 可用结束检查强制独立验收，不需要把 benchmark 谓词加入核心。

## 验证自己的扩展

先用 fixture 或假服务器检查参数、输出、未知结果、取消和停止确认。动作工具还应确认无效输入零派发、同一调用不重复执行、迟到结果不能恢复控制。随后使用实际场景评估模型选择与物理效果。新增工具的测试允许多种合理调用顺序。
