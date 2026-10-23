# 结果验证与采纳

验证针对目标和证据。控制器执行结束、工具返回 ok、目标实现分别记录。

## 主 Agent 与独立后端

默认 `verification.mode: main_agent`。主 Agent 在正常回复中同时提交判断和下一次调用：

```json
{
  "assessments": [{
    "call_id": "pick-call",
    "goal_id": "cup_held",
    "verdict": "satisfied",
    "reason": "当前观测支持杯子已被持住",
    "evidence": ["frame-after-pick"]
  }],
  "calls": [{"name": "robot.place", "arguments": {"object": "cup_1"}}]
}
```

call_id 与 goal_id 至少提供一个；未配置命名目标时继续使用旧动作判断。
整个回复先校验，错误引用不会导致部分判断或部分动作被接受。

显式配置 independent 才创建 reviewer。自动评估只针对 `review_outcome=True` 的动作。
`session.assess(call_id, evidence=[调用 ID])` 可复核动作；声明目标后也提供
`verification.assess(goal_id, evidence=[调用 ID])`。验证器输入包含原始结果、补充观测、
前后图片与目标，不含规划工作摘要或可执行工具。

reviewer 可继续返回 OutcomeAssessment，也可返回：

```python
AssessmentResult(status="ok", assessment=OutcomeAssessment(
    verdict="unknown", reason="物体被遮挡", evidence=["frame-1"]))
AssessmentResult(status="unsupported", reason="该方法不支持持物判断")
AssessmentResult(status="error", reason="模型服务不可用")
```

后两种情况不携带有效判断，记录为 run_status=unsupported/error、verdict=unknown。
不自动更换验证器、不重放动作。主 Agent 和独立来源的完整判断历史分别保留。

## 可选推进与完成条件

默认 policy 为空，判断仅供 Agent 参考。启用示例：

```yaml
verification:
  mode: main_agent
  policy:
    goals:
      cup_held:
        description: 杯子 cup_1 已稳定持住
        subject: cup_1
        max_age_s: 5
      cup_placed:
        description: 杯子 cup_1 已放在托盘内并松开
        subject: cup_1
        max_age_s: 10
    before_tools:
      robot.place: [cup_held]
    subject_arguments:
      robot.place: object
    completion: [cup_placed]
```

上述工具名是扩展示例，需由对应 provider 注册。subject_arguments 表示将该工具的顶层
object 参数与配置目标的 subject 对照；固定对象的无参数工具可以不配置绑定。
同一工具所要求目标的 subject 应与绑定参数一致。

运行时要求有效、未过期、非历史的观测或判断；如果引用判断，递归检查依据中不能含预测、
未知或冲突判断。只引用工具调用成功不足以通过严格验收。严格验收要求有关物理执行已结束，并使用结束后的证据。后端可通过 execution.ended_at
声明实际结束时间；缺失时保守使用运行时收到结束回执的时间。缺少采集时间的旧图片可用于建议模式，但不能通过严格时效检查。

同一来源按观测时间选择结论；异源当前结论冲突时不推进。新的不确定／不可用结果会阻止
沿用旧的肯定判断，晚返回的失效证据不会推翻新观测。动作派发后旧环境前提失效，下一步
需要适用的新证据。要求不满足时反馈给 Agent，由它选择补充观测、调整方案或报告阻塞。

`finish_check` 继续提供额外业务验收，在声明的完成条件通过后执行。代码只检查契约关系，
视觉准确性、持续稳定持物等真实效果仍需任务评估，不能由字段齐全推出。

## 记录与恢复

状态快照保存每次判断的来源、运行状态、目标与证据。Session.outcomes 保留旧的最新判断
兼容视图；严格条件使用完整来源历史，不把主 Agent 的一次改写当作覆盖独立来源。
续接会话的旧证据标为 historical，不能直接用于新动作的前提或任务验收。
模型上下文只发送近期摘要，完整内容通过 state.read 和 session.read/search 检索。
