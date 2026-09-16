# 后端与模型接入约定

## 1. 先接观测

`Observation` 包含事实、图像路径、本体状态、坐标系信息与可用传感器。真实观察器读取感知/机器人反馈，再转换为对象 ID 稳定的事实。

- `holding:right:blue_cup`：持物确认，而非“GRASP 已发送”。
- `ready:blue_cup`：当前握法落在后续技能验证过的入口范围。
- `reachable:bowl`：由几何/控制模块产生，不能用语言判断代替。
- `event:poured:blue_cup:bowl`：材料变化等历史证据，无法确认时输出 UNKNOWN。
- `healthy`：由驱动/监控层输出，并设置合适的有效期。

丢失目标时可保留旧位置并降低可信度/缩短有效期，也可明确发送 UNKNOWN。不同动作后端必须共享同一真实场景，不能各自维护想象的持物状态。

当前验证器检查新鲜证据与声明效果。稳定放置、多帧开盖确认、材料量变化等物理验收算法应放在观察器中；不要复制 `candidate.effects` 作为真实观测。mock 的效果转换仅用于符号调试。

## 2. 接入规划模型

实现异步 `client(context) -> dict`，传给 `StructuredPlanner`：

```python
from primitive_vla.agent.loop import Agent
from primitive_vla.agent.planner import StructuredPlanner

planner = StructuredPlanner(client)
agent = Agent(planner, executive, observer)
report = await agent.run(task)
```

上下文提供剩余目标、对象描述、当前事实、状态版本、合法候选、最近结果和剩余预算。返回值只能是：

```json
{"action": "skill", "index": 0}
```

或 `{"action": "observe"}` / `{"action": "stop", "reason": "..."}`。索引仅对本次上下文有效。未知索引和任意动作代码不执行；执行器还会读取新观测检查入口条件。

[`agent/task_parser.py`](../src/primitive_vla/agent/task_parser.py) 中的 `TaskParser` 实现自然语言解析，由 `runtime/session.py` 组装：配置 `parser.type=vlm` 后，模型输出与 `examples/pack_fruit.json` 相同的目标结构，再交给 `parse_task()` 验证。任务目标与下一步技能始终分开。

## 3. 接入 VLA / RL / 运动规划后端

在 `src/primitive_vla/backends/` 增加适配器，实现 [`backends.base.Backend`](../src/primitive_vla/backends/base.py) 的五个异步方法：

| 方法 | 职责 |
|---|---|
| `observe()` | 返回观测，不根据已发送命令推测效果 |
| `warmup()` | 准备可选模型资源；mock / HTTP 无需预热时直接返回 |
| `close()` | 释放本任务资源，可重复调用 |
| `run(call, candidate, lease, authority, feedback)` | 执行技能、输出阶段反馈、返回执行状态 |
| `stop(call_id, reason)` | 后端完成停止/交接后返回 `True`；无法确认则返回 `False` |

执行权限实现位于 `execution/control.py`，生命周期管理位于 `execution/executive.py`。组装应用时注册 `Executive(..., backends={"pi05": your_backend})`，把对应 `SKILL.md` 契约的 `backend` 改成 `pi05`。GRASP 和 PLACE 可以共享后端，STIR 可映射到另一个双臂策略。提供 `model_version` 用于日志。

建议先接 GRASP → PLACE。适配器内部需要明确：

1. 对象绑定如何转换为训练时支持的条件或指令。
2. 相机、状态字段、时间戳、预处理与 checkpoint 如何对齐。
3. 反归一化、关节映射、单位和动作语义如何对应真实硬件。
4. 未参与技能的肢体如何保持正确目标，不能直接把通道置零。
5. 技能何时终止；一个 action chunk 完成不等于技能结束。
6. 交接时清理旧动作队列与策略缓存，保留真实持物状态。

此版不假定 26 维动作、图像大小或任何 π0.5 SDK 字段。

## 4. 最终动作派发检查权限

`lease` 带调用 ID、执行代次和资源列表。推理请求及返回动作块都携带同一标识，派发时使用：

```python
accepted = authority.apply(
    chunk.call_id,
    chunk.generation,
    lambda: robot_driver.dispatch(chunk),
)
```

`chunk` 和 `robot_driver` 由实际适配器定义。`dispatch` 应快速提交，不能阻塞等待推理。网络下游还必须在机器人端检查代次，Agent 进程内检查无法阻止已进入远端队列的旧动作。

`stop()` 使用独立停止通路；动作权限撤销后仍须清理队列，完成当前接触状态对应的处理。确认驱动不再接受旧动作、达到允许交接的状态后才能返回 `True`。否则 Executive 锁定资源并停止任务；此版没有自动解锁接口。

## 5. 真机功能边界

当前运行监控由执行器的异步循环完成，不调用规划模型。它与 Agent 同处一个 Python 进程，不能抵御整个进程卡死、网络失联或控制器故障。真实驱动侧 watchdog、停止语义和紧急处理需独立实现。

当前仅支持 `boundary=atomic`；`local` 在加载配置时拒绝，后端接口不再包含未实现的 `prepare()`。

重启后应重新读取真实状态并核对历史不可逆操作。SQLite 保存任务与事件，CLI 可导出 JSONL；当前不会重放动作恢复执行权限，也不保证物理效果“恰好一次”。

## 6. 已实现的 HTTP 网关协议

配置后端 `type=http` 即可使用 [`backends/http.py`](../src/primitive_vla/backends/http.py) 中的 `HTTPBackend`。这是本项目自定义协议，不是 PhyAgentOS Forge 协议。网关需实现：

| 路由 | 请求 / 返回 |
|---|---|
| `GET /observation` | 返回下述 Observation JSON；每个事实带传感器来源和时间 |
| `POST /calls` | 接收 `{call: SkillCall, generation: int, resources: string[]}`；接受后返回 `{call_id: string}` |
| `GET /calls/{call_id}` | 返回 `{call_id, generation, status, phase, control_healthy}` |
| `POST /calls/{call_id}/cancel` | 接收 `{reason: string}`；实际停止确认后返回 `{call_id, stopped: true}` |

执行状态可为 `accepted / running / completed / failed / timed_out / cancelled / rejected`。`completed` 只说明网关执行结束，Agent 随后独立获取 Observation 验收效果。取消请求被接受不等于已经停止；缺少 `stopped: true` 会保持控制锁定。

观测示例（实际需包含技能使用的全部事实）：

```json
{
  "timestamp": 1789440000.0,
  "facts": {
    "healthy": {"value": "true", "source": "driver:health", "timestamp": 1789440000.0, "confidence": 1.0, "ttl_s": 1.0},
    "holding:right:blue_cup": {"value": "unknown", "source": "perception:occluded", "timestamp": 1789440000.0, "confidence": 0.3, "ttl_s": 2.0}
  },
  "images": {"front": "https://camera.example/frame.jpg"},
  "proprioception": {},
  "frames": {"front": "camera_front"},
  "sensors": ["front", "joint_feedback"]
}
```

网关必须按调用 ID 去重，按执行代次屏蔽旧动作，并在自身驱动侧维护独立 watchdog。Agent 不会重试 POST 动作请求；网络失败时先请求停止，再观察真实效果。观察与控制应共享同一个机器人环境。

## 7. SAPIEN 仿真后端

`backends/sapien.py` 实现同一个后端协议，通过 JSON Lines 调用 RoboTwin Python 环境中的 `simulation/worker.py`。GRASP、PLACE、STIR 使用真实物理接触、关节驱动和位姿观测。配置、场景范围与运行证据见 [simulation.md](simulation.md)。

## 7. 子进程通信与资源生命周期

SAPIEN、通用 π0.5 和 LIBERO 客户端统一使用 `runtime/process.py` 的 `JsonProcess`。适配器负责消息内容与证据转换，不再分别维护进程、响应读取或 pending future。

- 启动支持 worker 主动返回 `id=0`，或客户端发送 `id=0, op=init` 两种握手。
- 请求按 ID 对应响应；`event` 消息交给适配器回调。启动成功只发生一次。
- 单次超时或取消移除该请求的等待者；迟到响应被忽略，保留后续停止请求的通信通路。
- 协议错误、EOF 会使所有等待请求失败；后续请求立即报错，不自动重启物理世界。
- 关闭发送 `close`，最多等待 5 秒后强制终止并回收，最后关闭读取任务与日志。关闭中的取消会等待回收完成后传播。

超时值由使用方显式提供，策略统一但不混淆控制预算：

| 使用方 | 启动 | 普通请求 |
|---|---|---|
| SAPIEN | `simulation.startup_timeout_s` | 沿用 Executive 的调用/停止预算，传输层不额外截断长动作 |
| 通用 π0.5 | `vla.startup_timeout_s` | `vla.inference_timeout_s` |
| LIBERO world / policy | `libero.startup_timeout_s` | 沿用同一个配置值；Executive 仍可提前取消 |

Runtime 统一调用观察后端的 `warmup()`，退出时通过 `runtime/lifecycle.py` 的 `close_all()` 关闭模型客户端与所有后端。即使一个资源关闭失败，其余资源仍完成关闭后才传播异常。`close()` 释放资源，技能运行中的物理停止确认仍由 `stop()` 负责。

旧 HTTP 网关可继续返回额外字段，但内部 `SkillFeedback` 不再转发未使用的 `cancellable`。取消行为仍依据取消请求、控制权限撤销和后端停止确认，不读取该标志。
