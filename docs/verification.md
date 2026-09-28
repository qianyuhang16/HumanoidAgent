# 动作结果判断

默认由主 Agent 在正常下一轮决策中判断动作结果。它仍使用同一会话的任务、说明、历史、memory 和图片；不会另开一次验证请求。工具正常结束与用户目标达成是两件事。

## 主 Agent 模式（默认）

```yaml
verification:
  mode: main_agent
```

模型在原来的 `calls` 或 `final` 回复中，可以同时携带 `assessments`：

```json
{
  "assessments": [{
    "call_id": "previous-action-id",
    "verdict": "satisfied",
    "reason": "前后图显示物体已放到指定区域并松开",
    "evidence": ["previous-action-id:after:front"]
  }],
  "calls": [{"name": "camera.observe", "arguments": {}}],
  "final": null,
  "memory": "放置已完成，继续处理下一目标"
}
```

`assessments` 可省略，不需要为提交判断额外调用工具或多用一轮决策。每条判断包含动作 `call_id`、`verdict`、`reason`、`evidence`，可选 `goal` 描述具体最终条件；省略时沿用已有 goal 或工具说明。

`satisfied`、`unsatisfied`、`unknown` 分别表示已达成、未达成、无法确认。框架校验结构及调用/图片引用是否存在，不裁定这些语义判断是否正确，也不据此禁止下一动作或否决最终回答。主 Agent 结合证据决定观察、重试、继续或结束。原有可选 `finish_check` 是另一独立配置，默认未启用。

同一条回复中的所有判断会先一起校验。引用不存在时，整条回复进入格式纠正，不执行其中的动作、不提交部分判断，也不重放之前的动作。

## 独立验证模式

```yaml
verification:
  mode: independent
  reviewer:
    adapter: primitive_vla.verification.model:JSONOutcomeReviewer
    options:
      base_url: http://127.0.0.1:8000/v1
      model: your-vision-model
      max_images: 6
      max_tokens: 8192
```

必须显式配置 reviewer。主 Agent 模式即使保留了 reviewer 配置，也不会构造或调用它。切换模式不重新执行已有动作，也不自动重新验证历史记录。

声明 `ToolDefinition(review_outcome=True)` 的动作执行后，独立模式会调用 `assess(OutcomeContext) -> OutcomeAssessment`。构造器仍为 `(options, PluginContext)`，可换成其他 VLM、世界模型或实测规则；模型实现位于 `verification/model.py`。

验证输入只有任务、任务说明、原始调用、工具说明、实际结果、补充证据和图片，不含主 Agent 的 working_memory、完整对话历史或可执行工具。即使配置相同的 DeepSeek 模型，这也是一次独立请求。它返回 verdict、reason、evidence 和可选 goal。预测模型的预测需要在理由中明确，不能当作实测事实。

独立模式另提供可选的 `session.assess(call_id, evidence=[记录中的工具调用ID])`，用于补充观察后再次判断。调用它不是继续执行的前提。独立判断只是主 Agent 的决策依据；主 Agent 也可以提交自己的修订，来源会分别记录。

验证出错、超时或取消会保存 `unknown` 及原因，不重跑动作，不悄悄切换验证模式。自动验证计入任务总时间，不消耗主 Agent 决策轮数；显式 `session.assess` 占一次工具调用。额外模型调用只发生在 independent 模式。

## 证据与记录

- 动作工具返回 `before_images` 与 `images`，用一致的相机名表达执行前后图。`review_outcome=True` 请求建立待判断记录，main_agent 模式下不调用验证器；主 Agent 也可判断其他已执行动作。
- 图像预算优先保留一组同视角前后图，再填充最新观察，并去掉相同文件的重复附件。`max_images >= 2` 且有对应视角时才可保留完整图对。图片标签明确动作 ID 和 before/after，避免把旧图当作当前状态。
- `Session.outcomes` 按动作调用 ID 保存当前判断，包含 source（main_agent/independent，尚未判断时为 null）、revision、理由和证据。完整修订保留在追加历史中，memory 更新不会覆盖记录。
- 后续聊天恢复历史判断并标为 historical；旧图片不会自动作为新现场图附带。真实工具句柄不恢复。旧判断可修订，不成为永久动作锁。
- 网页显示判断来源及前后图片。判断仍可能出错；结果可追溯不等于视觉判断一定准确。

## 代码位置与离线验证

```text
src/primitive_vla/verification/
  contracts.py   # 判断与独立验证输入的类型
  outcomes.py    # 记录、引用校验和修订历史
  images.py      # 前后图配对与图片预算
  runtime.py     # 主 Agent / 独立验证的接入
  model.py       # 可替换的独立 JSON 模型
```

已有 `effects.py`、`rules.py` 继续服务旧场景适配，不参与上述模式选择。Agent 主循环仅提交判断和传递动作结果，不包含机器人或任务专用判定。

```bash
.venv/bin/python -m unittest tests.integration.test_verification -v
```

测试使用假工具、假模型传输：覆盖两种模式的请求数、同轮判断与继续、前后图预算、引用纠正、结果修订、模式切换、取消及模型替换，不连接真实机器人或模型服务。
