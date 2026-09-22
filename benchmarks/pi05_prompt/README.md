# π0.5 电池提示词与通用 Agent 试验

模型：`Myungkyu/pi0_5_rmbench_preset_b64_60k`，精确版本见 `configs/vla-lab.json`。
这是社区 RMBench 子任务微调模型；官方 RMBench 的原始 executor 是 Mem-0。
“发布文本”指转换训练数据中的子任务标签，不把它混称为官方 π0.5 基线。

## 复现

先启动独立页面（模型常驻）：

```bash
PYTHONPATH=src .venv/bin/python -m uvicorn primitive_vla.web.vla_app:create_app --factory --host 127.0.0.1 --port 8087
PYTHONPATH=src .venv/bin/python benchmarks/pi05_prompt/direct.py
```

直接评测会操作此页面的会话，期间请勿手动执行/重置。每个条件从相同种子重置，
使用相同动作预算。每次重置会保存上一场景的视频。
完成直接评测后，关闭该服务以释放 GPU，再执行 Agent：

```bash
PYTHONPATH=src .venv/bin/python benchmarks/pi05_prompt/agent.py
```

Agent 复用原有 `grounding_sam`、`ik`、`vla` 三个通用 SKILL.md 和已有逐步规划提示词。
没有新电池技能、预写的子任务顺序、VLA 文本模板或模拟器状态反馈。
`observe`/`finish`/`stop` 是通用控制接口。是否调用感知、IK，以及如何组织 VLA 文本，
由模型根据 RGB 与工具返回决定。模拟器成功信号仅在 Agent 结束后单独评分。

## 产物

`workspace/pi05/evaluation/direct/`：配对提示词、配置、种子、动作数、关节反馈、
最终诊断和场景路径。场景包含逐帧 PNG、动作 JSONL 和 MP4。
`workspace/pi05/evaluation/agent/<seed>/`：完整规划与工具日志、图像、视频、最终评分。

这是两个种子的开发试验，不是官方 benchmark 复现；没有历史关键帧检索，
渲染使用当前网页的 raster 配置。子任务“放中间”和整任务“通电”分别报告。
XY 对槽不能单独作为完全插入、松爪或抓持成功证据。
