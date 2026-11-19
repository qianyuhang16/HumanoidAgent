---
id: state.robocasa.localize_pixel
version: '1.0'
description: 根据当前相机深度将指定像素回投为世界坐标，不承担目标检测或抓取规划。
tool:
  domain: state
  input_schema:
    type: object
    properties:
      image:
        type: string
        format: image-ref
        description: 使用图片旁的引用，例如 image_1；相机和帧由系统关联。
      row:
        type: integer
        minimum: 0
      column:
        type: integer
        minimum: 0
    required:
    - row
    - column
    - image
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
    operation: localize_pixel
---
# 把像素投影到世界坐标

## 能力与适用条件
已取得同一 episode、相机和仿真步的图像，并明确选中了目标表面像素。

## 所需输入与准备
传入 image（例如 image_1）、row、column。系统根据图片引用找到对应相机和帧，像素必须在实际图像范围内。

## 调用方法
使用从图像左上角开始的行列索引。返回 point_world，单位米；场景归属、图像边界和帧是否过期由平台检查。

## 完成验证
结合图像确认坐标对应目标，背景像素和表面点不能直接当作可执行抓取姿态。

## 失败处理
若步号过期、episode 不符或越界，重新采图并修正引用；不要重用动作前的过期坐标。
