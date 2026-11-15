---
id: execution.robocasa.set_gripper
version: '1.0'
description: 保持末端位姿，在指定步数内执行夹爪开合命令。
tool:
  domain: execution
  input_schema:
    properties:
      gripper:
        description: 'Gripper command: +1 close, -1 open (default +1)'
        type: number
        minimum: -1
        maximum: 1
      steps:
        description: Number of env steps to hold (default 10)
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
    operation: set_gripper
---
# 控制夹爪

## 能力与适用条件
用于打开、闭合或调整夹持，需要可靠的目标与接触信息。

## 所需输入与准备
gripper 在 [-1,1]，+1 闭合、-1 打开；steps 为步数。

## 调用方法
根据物体选择开合幅度和时间。持续闭合可能挤出或损伤小物体，不应把闭合命令视为持物证明。

异步返回 call_id 后等待终态；simulation 资源冲突由 Execution 拒绝。暂停 Planning 不会停止动作，取消必须等待停止确认。

## 完成验证
读取新状态与图像，必要时使用任务验证；夹爪动作结束不证明已抓住目标。

## 失败处理
没有抓住或夹持不稳时观察对象位置，决定重新对齐、释放或求助。
