---
id: state.robocasa.capture_image
version: '1.0'
description: 采集当前 RGB 与可选米制深度，并返回可读取的资源引用。
tool:
  domain: state
  input_schema:
    type: object
    properties:
      camera:
        type: string
        enum:
        - agentview
        - agentview_right
        - wrist
        - navview
      depth:
        type: boolean
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
    operation: capture_image
---
# 采集相机图像

## 能力与适用条件
需要目标识别、视角判断、像素定位或动作证据时使用；桌面预览不属于正式任务证据。

## 所需输入与准备
选择 agentview、agentview_right、wrist 或 navview；depth 控制是否保存深度。缺省为前视 RGB。

## 调用方法
读取 resources.rgb 的实际图像字节；只收到资源 ID 不等于看到了图像。采集在原生线程排队，不保证动作期间立即返回。

## 完成验证
检查图像尺寸、camera、episode_id、simulation_steps；需要定位时保留这些信息。

## 失败处理
遮挡或视角不足时决定调整机器人或重新采集；不得从缺失画面猜测目标位置。
