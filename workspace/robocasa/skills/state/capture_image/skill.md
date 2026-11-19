---
id: state.robocasa.capture_image
version: '1.0'
description: 采集并直接查看当前 RGB 图像，可选返回米制深度资源。
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
调用完成后，RGB 图片直接附在工具结果对应的模型输入中，查看图片即可，无需额外读取资源。
图片旁显示相机名称、尺寸和稳定引用（例如 image_1）。定位像素时引用该图片，不抄写场景 ID 或步号。
采集在原生线程排队，不保证动作期间立即返回。可选深度保留为资源。

## 完成验证
依据图像与相机名称判断视角；需要定位时传入图片引用和像素行列。动作后需要新画面时重新采图。

## 失败处理
遮挡或视角不足时决定调整机器人或重新采集；不得从缺失画面猜测目标位置。
