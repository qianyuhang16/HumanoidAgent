# 独立 VLA 提示词实验台

入口：<http://127.0.0.1:8087/>。当前支持 `battery_try` 电池场景。

```bash
PYTHONPATH=src .venv/bin/python -m uvicorn primitive_vla.web.vla_app:create_app \
  --factory --host 127.0.0.1 --port 8087
```

## 使用方式

1. 等待状态显示“就绪 · 模型常驻”。服务启动时自动加载一次模型。
2. 输入指令，或点“填入官方原句”，设置动作上限，点击执行。默认不因分类器信号提前结束；勾选“按模型结束信号提前停止”可恢复原有交接行为。
3. 右侧显示实时仿真相机；下方保留各次原始提示词、动作数和结束原因。
4. 比较不同表述前，用相同种子重置场景。重置保留同一个 executor 和模型权重，重置策略记忆与采样随机种子。
5. 不重置就执行下一条指令，会延续当前物理状态，但每条新指令都会清空策略时序记忆。停止在当前在途推理/动作批次结束后生效。

路径是 **用户原始文本 → Mem-0 executor → 仿真关节动作**。没有 Agent 改写、规划、IK 或 Grounding/SAM 调用。图像采用普通渲染；网页约每 0.5 秒请求最新相机帧，并非固定帧率视频。

当前加载的是 **Mem-0 `battery_try/final_step30000` 任务 checkpoint**，Qwen3-VL-2B-Instruct 是其中的视觉语言骨干；这不是未经任务训练的通用 base，也不是 π0.5。

## RMBench 官方如何给 executor 提示词

以下依据当前安装的官方源码，区分示范标注与评测时的指令来源。

### 示范标注

[`battery_try.py`](../workspace/mem0/vendor/RMBench/envs/battery_try.py) 的 `play_once()` 在抓取、抬起、放置等示范动作上标注同一条 `language_annotation`：

> Use dual arm to pick up the batteries and place them into the battery slots in the positive direction.

源码还包含指定左/右电池、正/负方向的子任务标注。这些是示范程序写入的语言标签，不代表模型可以同样可靠地理解任意同义表述。

### 官方评测

[`deploy_policy.py`](../workspace/mem0/vendor/RMBench/policy/Mem-0/deploy_policy.py) 的 `eval()` 区分：

- **Mn 长任务**：Planner 根据图像及历史生成回答，提取 `next_subtask:` 后的文本作为当前 instruction。结束分类器达到交接条件后再次调用 Planner。
- **M1 任务**：直接读取配置中的 `global_task` 作为 instruction。

[`memorymatters_agent.py`](../workspace/mem0/vendor/RMBench/policy/Mem-0/source/agent/memorymatters_agent.py) 的 `get_instruction()` 实现子任务提取，`update_obs()` 把当前 RGB 与 instruction 交给 Qwen 编码，随后动作头结合视觉语言特征、时序记忆和机器人状态生成动作。executor 不通过自然语言回答来控制机械臂。

实验台用人输入的文本替代这个 instruction 字段，保留低层执行器。按钮中的“官方原句”特指上面的示范标注，不保证官方 Planner 每次都生成完全相同的字符串。

## 结果与能力边界

- 分类器的“结束”只表示交回控制，不是成功判定；可能很早触发。独立实验台默认仅记录此信号，继续到动作预算、场景动作总限额或手动停止。Agent 的默认交接行为保持原样。
- 页面展示的入槽、极性和仪表诊断来自仿真器，只供人查看，不反馈给模型。官方入槽判据主要检查 XY 距离小于 2 cm，不能单独证明完全插入、正确高度或松爪。
- 比较措辞应使用相同场景种子、动作预算及重置流程，并在多个种子上重复。仅看到一轮失败不能推断模型完全不理解该提示词。
- 初版错误地沿用了 Agent 的“两次结束信号即交接”规则，独立入口没有 Planner 接手，导致每次只执行 2 步。已将是否根据结束信号停止改为显式选项，默认关闭。分类器本身为何过早触发尚未定位；修复执行截断不等于证明动作模型已能完成装配，也不能据此判断提示词鲁棒性。

运行记录位于 `workspace/vla-lab/<时间>/`：`commands.json` 保存指令结果，`events.jsonl` 保存调用事件，每个 `scene-*` 保存相机画面及仿真记录。上线验证记录见 `workspace/vla-lab/qa.json`。

## 两步截断修复验证

同一官方提示词、种子 `300100`，修复后的独立入口实际执行了完整 **450 / 450 步**，期间结束分类器仍触发，但没有再截断执行。最终左右电池满足官方 XY 对槽判据，仪表未亮，因此不将该回合记为装配成功。回归测试同时覆盖启用结束信号时两步交接、关闭时跨动作批次继续执行。

## 代码职责

| 模块 | 职责 |
| --- | --- |
| `web/vla_app.py` 与 `web/static/vla.*` | HTTP 入口、提示词表单、实时画面与记录 |
| `runtime/vla_lab.py` | 常驻会话、单指令执行、停止和场景重置 |
| `simulation/vla_lab_world.py` | 保留 executor 重建场景、展示用诊断 |
| `backends/rmbench_tools.py` | 复用动作分块执行与停止交接 |
| `vla/mem0.py` | 已有 Mem-0 权重加载与动作推理 |
