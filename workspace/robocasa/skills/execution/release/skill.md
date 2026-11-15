---
id: execution.robocasa.release
version: '1.0'
description: 保持末端位置并打开夹爪，释放当前物体。
tool:
  domain: execution
  input_schema:
    properties:
      steps:
        description: Number of env steps (default 10)
        type: integer
        minimum: 1
        maximum: 2000
    type: object
    additionalProperties: false
  output_schema:
    type: object
  provider: rpent.robocasa
  supported_robots:
  - robocasa.PandaOmron
  required_resources:
  - simulation
  execution_mode: async
  timeout: 120.0
  entrypoint: runtime.environment:NativeTool
  options:
    operation: release
---
# 松开夹爪

## 能力与适用条件
计划放置或退出夹持时使用；应先确认目标区域、承托和释放高度。

## 所需输入与准备
steps 指定打开夹爪持续的仿真步数。

## 调用方法
调用后等待动作结束；释放前的条件判断由 Agent 结合场景决定。

异步返回 call_id 后等待终态；simulation 资源冲突由 Execution 拒绝。暂停 Planning 不会停止动作，取消必须等待停止确认。

## 完成验证
重新观察物体是否落在目标区域，再按需调用独立验证。

## 失败处理
物体未释放、掉落或落点不符时依据证据重新观察和规划，不声称放置成功。
