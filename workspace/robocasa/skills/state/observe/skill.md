---
id: state.robocasa.observe
version: '1.0'
description: 读取最近完成仿真步的机器人位姿和步号，不推进仿真。
tool:
  domain: state
  input_schema:
    type: object
    properties: {}
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
    operation: observe
---
# 读取当前状态

## 能力与适用条件
任务开始、行动前后或需要确认状态时使用。动作期间读到的是最近完成步的缓存。

## 所需输入与准备
无需参数。位姿为世界坐标；任务目标以用户指令为准。

## 调用方法
调用本工具并阅读 robot_state 和 simulation_steps。需要图像时选择相机采集工具。

## 完成验证
结合步号检查状态变化；状态缓存只表示观察事实，不证明抓取或导航成功。

## 失败处理
缓存不足以作决策时补充相机观察；旧 Session 绑定其他 episode 时应新建任务，不能重放动作。
