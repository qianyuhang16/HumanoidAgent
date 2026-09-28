# 从图片像素到 IK

日常只使用一个入口 `primitive_vla_cobotmagic.ik`，一个配置 `configs/ik.yaml`。
网页和命令行共用相机标定、像素换算、补偿、路径搜索和计划导出；实际执行仍通过原有 ROS 桥。

## 直接输入像素

在仓库根目录执行。`U V` 是 **front 原图的像素坐标**，左上角为 `(0, 0)`，不是网页缩放后的坐标。`--arm` 必须明确指定。

```bash
# 只规划，读取当前图像与关节状态，不发动作
.venv/bin/python -m primitive_vla_cobotmagic.ik --arm right --pixel 320 240

# 同一个入口：规划完成后，在终端按空格开始执行
.venv/bin/python -m primitive_vla_cobotmagic.ik --arm right --pixel 320 240 --execute
```

像素应来自同一固定 front 相机、同分辨率且场景未变化的画面。程序会重新读图和状态，保存此次使用的 `front.jpg`。现场物体移动后需重新给坐标。

默认先规划，只有显式 `--execute` 才打开运动发送能力，且仍等待空格；Esc/Ctrl+C 取消。没有自动使能、复位或抓取操作。

## 网页与已保存计划

```bash
# 同一个入口启动只读标点网页
.venv/bin/python -m primitive_vla_cobotmagic.ik --web

# 离线复核已保存计划
.venv/bin/python -m primitive_vla_cobotmagic.ik --plan workspace/cobotmagic-ik/click-plans/<记录目录>

# 执行已冻结的计划，仍按空格启动
.venv/bin/python -m primitive_vla_cobotmagic.ik --plan workspace/cobotmagic-ik/click-plans/<记录目录> --execute
```

打开 http://127.0.0.1:18085 。网页只取图、标点、规划和生成同一入口的命令，没有运动 API。页面从共享配置读取高度、时间、角度及补偿，不再显示写死的参数。启动后改配置需重启服务。

黄色十字为原始像素；绿圈为补偿后的目标，绿线为实际 TCP 路径投影。“连续关节路径（非直线）”表示直线规划失败后使用了终点引导的备用路径。

## 参数

统一编辑 `extensions/local/cobotmagic/configs/ik.yaml`，也可以给所有入口加 `--config /path/to/ik.yaml`。

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `target_base_z_m` | `0.17` | 所选机械臂 base 下的目标高度，米 |
| `forward_compensation_m.left/right` | `0.10 / 0.15` | 图片向上对应水平面的补偿距离，米 |
| `orientation` | `downward` | `downward`、`position`、`current` 或 `rpy` |
| `rpy_deg` | `[0,0,0]` | `rpy` 模式的 Roll/Pitch/Yaw，度 |
| `motion.move_duration_s` | `3` | 指令轨迹时长，秒 |
| `motion.downward_angle_range_deg` | `[40,90]` | 朝下角度范围，90 度为竖直向下；优先求范围上限 |
| `motion.preserve_jaw_heading` | `true` | 保持起点两指连线在水平面中的方向 |
| `motion.jaw_heading_tolerance_deg` | `2` | 路径与终点的两指朝向允许误差，度 |
| `motion.speed_percent` | `30` | 原桥驱动速度百分比 |
| `motion.settle_timeout_s` | `3` | 轨迹发完后等待反馈收敛的时间 |
| `motion.position_tolerance_m` | `0.008` | 最终 TCP 位置误差阈值，米 |
| `motion.orientation_tolerance_rad` | `0.04` | 固定姿态模式的终点角度误差，弧度 |
| `motion.feedback_limit_tolerance_deg` | 当前配置 `2.0` | 覆盖已记录的右腕 1.269° 回读越界；不扩大 IK/指令范围，省略时库默认 0.5° |

桥地址、URDF、关节名称、标定工作区和通信参数也在该文件。左右臂内外参及 TCP 从标定工作区各自当前通过的批次自动加载。

角度搜索先尝试范围上限，按 5 度下降，再按 1 度细化；这是有界局部搜索，不承诺全局最优。每个角度先尝试直线路径，再尝试终点引导的连续关节路径。时间重新参数化仍检查 URDF 的速度上限；指令时长不等于实际机器人恰好到位的时间。

求解器相邻路点的关节差不再受原先 0.15 rad（约 8.6°）的一刀切限制。复位附近的腕部解可能跨过这一数值，但实际轨迹会按不超过 0.003 rad 的关节步长细分，逐点检查 TCP 范围和夹爪水平朝向，再检查三秒轨迹的关节速度；采用这些实际轨迹结果判断，不把数值求解器路点差直接当成发送给机器人的跳变。

零位附近，ROS 反馈可能是略小于 0 的 joint2 或略大于 0 的 joint3。原始反馈用于 FK、起点复核和到位判断，不改写；在上述容差内，仅把 IK 初始种子和保持指令投影到原有限位边界，沿用现有桥的限幅约定。生成的轨迹和显式关节目标仍须在原有限位内，规划记录中以 `start_joint_limit_projection_rad` 标出初始修正量。超出反馈容差会直接报告臂、关节、反馈值和限位，且不会误报成目标点不可达。该参数不是关节零位校准，也不应用来掩盖明显的模型/反馈不一致。

## Python 调用

```python
from primitive_vla_cobotmagic.ik import move_to_pixel

result = move_to_pixel("right", 320, 240)  # 默认只规划
# execute=True 时仍在调用终端等待空格
# result = move_to_pixel("right", 320, 240, execute=True)
```

异步调用方可使用 `ik.pixel.PixelIK(config).plan(arm, u, v)` 取图并规划，结束后关闭 `ik.bridge`。HTTP 适配器与命令行调用的就是这个模块。

## 记录与边界

每次生成独立目录，包含原图、关节快照、输入与补偿来源、冻结的 URDF/TCP/有效参数、`hover.yaml` 和 `plan.json`。`--plan` 使用冻结参数，后续修改全局配置不会改变旧计划；重新标点才会应用新配置。补偿只做一次。执行前重新核验起点及重建计划，机器人已移动则需重新规划。

`*.trace.jsonl` 和 `*.result.json` 保存执行反馈。使用现有 `get_observation` / `send_action`，不需要更新机器人端；报文复制当前双臂状态，仅替换所选臂六关节目标。夹爪保持沿用旧桥的 0–80 mm 转换，不新增开合操作。

RGB 坐标仍依赖高度平面估计，没有深度测量；本模块不做碰撞规划。已验证过的硬件流程保留，重构本身只做离线测试。

`cameras.yaml`、`calibration.yaml` 和 `ik.yaml` 分别负责相机问答、双臂标定和 IK；`agent.yaml` 引用已有配置和权重来组合 Agent 感知测试，见 [AGENT.md](AGENT.md)。旧 `ik_web`/`hover` 命令及工作区内冻结的执行配置仍可用；历史默认配置副本已移除。旧版本导出的计划若重建不一致，请重新生成。

## 离线测试

```bash
.venv/bin/python -m unittest discover -s extensions/local/cobotmagic/tests -p test_hover.py -q
.venv/bin/python -m unittest discover -s extensions/local/cobotmagic/tests -p test_ik_web.py -q
.venv/bin/python -m unittest discover -s extensions/local/cobotmagic/tests -p test_pixel_ik.py -q
```
