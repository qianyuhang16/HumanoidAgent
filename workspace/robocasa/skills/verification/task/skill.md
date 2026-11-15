---
id: verification.robocasa.task
version: '1.0'
description: 读取当前 episode 的原生成功谓词和进度，产生独立验证证据。
tool:
  domain: verification
  input_schema:
    type: object
    properties:
      related_call_id:
        type: string
        minLength: 1
    additionalProperties: false
  output_schema:
    type: object
  provider: rpent.robocasa
  supported_robots:
  - robocasa.PandaOmron
  required_resources: []
  execution_mode: sync
  timeout: 120.0
  entrypoint: runtime.environment:NativeTool
  options:
    operation: verify_task
---
# 验证原生任务目标

## 能力与适用条件
检查任务效果；判断某次动作的最终效果时应先等待该动作结束。属于仿真真值，不是视觉模型判断器。

## 所需输入与准备
可选 related_call_id，必须来自同一 episode、Session 和 Task 中已结束的动作。

## 调用方法
调用后阅读 conclusion、reason、evidence 与任务进度，并考虑原生任务目标是否覆盖用户目标。

## 完成验证
success 和 failure 为谓词结论；接口异常意味着验证未完成。不能从 Execution completed 推断成功。

## 失败处理
failure 作为下一轮决策证据，必要时重新观察或调整操作；异常不能伪造成 false 或 success。
