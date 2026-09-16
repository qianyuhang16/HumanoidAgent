# MOVE_TO + π0.5 动作控制

## 当前状态

已将物理控制分成两条路径：**MOVE_TO 使用 IK 接近目标；GRASP、PLACE、STIR 交给本机 π0.5 产生关节动作块。** 当前 `configs/sapien.json` 已选择这个配置，网页仍为 http://127.0.0.1:8081。

π0.5 base 已严格加载并实际控制了 Panda 仿真。首次实验中，MOVE_TO 成功，VLA 执行了 12 个动作块、60 个控制步，但没有抓起红色物体，最终状态 stopped / GRASP failure；停止得到仿真器确认。没有运行旧抓取脚本来补做。

这是一次跨机器人配置的 base 模型尝试，不是已适配的 Panda 策略。官方将 base checkpoint 列为微调起点，部署到具体机器人还需要匹配输入输出及数据处理：[OpenPI 模型说明](https://github.com/Physical-Intelligence/openpi#model-checkpoints)、[LeRobot π0.5 base](https://huggingface.co/lerobot/pi05_base)。

## 控制链

```text
Codex：选择 MOVE_TO(target=red)
  → SKILL.md 的 approach 程序
  → 根据目标仿真位姿计算接近点
  → IK 移动末端到物体上方，保持当前夹爪开度
  → 观察 near:red=true

Codex：选择 GRASP(object=red)
  → SKILL.md 的 policy_prompt
  → 相机图像 + 当前关节与夹爪状态 + 指令
  → 本机 π0.5 → 50×32 归一化动作
  → Franka 实验适配：反归一化、关节目标、夹爪闭合度
  → 截取前 5 步 → 关节限制 → 仿真关节驱动
  → 新观测 → 继续推理或按效果终止
```

VLA 控制路径没有调用 IK，没有 GRASP/PLACE/CIRCLE 的运动模板；`JointPolicyControl` 只接收模型转换后的关节目标并执行限幅。执行器仍检查技能条件与停止状态。

`near` 是末端与接近点的几何距离条件，不代表真正完成抓取。接近点使用仿真物体真实位置，因此整体仍不是纯视觉定位。VLA 本身收到 RGB、关节状态和指令，不接收精确物体位姿作为动作目标。

## 代码与技能

| 位置 | 职责 |
|---|---|
| `skills/sapien_vla/move_to/SKILL.md` | 唯一的 IK 接近程序；不控制夹爪开合 |
| `skills/sapien_vla/{grasp,place,stir}/SKILL.md` | 契约与 policy_prompt；program 为空 |
| `vla/pi05.py` | 严格加载 LeRobot 权重、归一化状态、构造输入、推理与动作转换 |
| `vla/worker.py` | 独立模型进程的 JSON Lines 服务 |
| `vla/client.py` | 异步加载/推理请求、进程回收；高层 VLM provider 保持独立 |
| `backends/sapien.py` | 按技能选择 IK 或 VLA，执行动作块并读取效果 |
| `simulation/policy_control.py` | 直接关节控制、关节范围与每步变化限制 |
| `simulation/worker.py` | policy_begin/chunk/end 与停止协议，拒绝过期和重复动作块 |
| `simulation/scene.py` | near 条件、物理状态与搅拌观测；不替代 VLA 生成操作动作 |

旧 `skills/sapien/` 仍作为脚本基线保留。当前默认使用 `builtin:sapien_vla`；选择旧技能源并令 `vla=null` 才会使用历史脚本控制。一个技能不能同时配置 policy_prompt 和 program。

## 权重、输入与实验动作解释

本机权重缓存：

```text
/home/estar/.cache/huggingface/hub/models--lerobot--pi05_base/
  snapshots/b211f3d44c36b6acfcf7ae94a64e8e96f75a64ba/
```

推理使用 `/home/estar/miniconda3/envs/pi/bin/python` 中已安装的 LeRobot；主服务、SAPIEN、π0.5 依赖互相隔离。本地 LeRobot 的 from_pretrained 会捕获部分加载错误后继续返回模型，因此本项目直接加载 safetensors，恢复确实共享的词嵌入权重别名，再做 strict=True 参数检查。缺少真实权重时不会继续随机初始化推理。

归一化统计来自官方 π0.5 base 的 [Franka assets](https://storage.googleapis.com/openpi-assets/checkpoints/pi05_base/assets/franka/norm_stats.json)，缓存于 `workspace/pi05/franka-norm-stats.json`。官方强调状态/动作定义与归一化统计必须匹配：[归一化说明](https://github.com/Physical-Intelligence/openpi/blob/main/docs/norm_stats.md)。该说明中的机器人约定不能自动证明 π0.5 base 与本场景的完全兼容性。

当前 `franka_experimental` 的明确约定：

- 状态前七维为 Panda 关节角，第八维为夹爪闭合度：`1 - (left_finger + right_finger) / 0.08`。
- 根据官方八维 `state.q01/q99` 归一化，再填充到 32 维；PI05 将状态离散化后放进语言输入。
- `observation.images.base_0_rgb` 使用真实仿真总览图像；缺失的两路 wrist 图像由模型接口 mask 掉，不复制总览图假装腕部图像。
- 模型输出 50×32，取前八维按 `actions.q01/q99` 反归一化。
- 当前 action_mode=joint_delta：前七维相对于**该动作块输入观测**的关节角，不在预测步之间累加；第八维解释为绝对闭合度，0 开、1 合。
- 模型推理返回整个动作块，默认只执行前五步，然后重新观察。关节目标经过每步变化限制和 Panda 关节范围裁剪。

这套关节增量与夹爪映射属于本次显式选择的实验适配，并未通过该 checkpoint 在 Panda 上的训练数据标定。预训练 Franka、Panda 夹爪、相机视角和场景分布的差异仍然存在；不能把加载成功等同于策略适配成功。用户可选择 joint_position，但不应仅为得到好看的轨迹任意切换，需根据相应 checkpoint 的训练定义确定。

## 配置与启动

现有机器启动：

```bash
.venv/bin/python -m primitive_vla serve \
  --config configs/sapien.json --workspace workspace/sapien
```

首次在其他工作区准备统计文件：

```bash
mkdir -p workspace/pi05
curl -fL https://storage.googleapis.com/openpi-assets/checkpoints/pi05_base/assets/franka/norm_stats.json \
  -o workspace/pi05/franka-norm-stats.json
```

`vla.checkpoint`、`norm_stats` 与 Python 路径需指向本机实际文件。模型进程启用本地离线读取，不会偷偷下载新权重。原有 Qwen 服务不是本配置的依赖。

| 参数 | 当前值 | 作用 |
|---|---:|---|
| `vla.action_mode` | joint_delta | 动作的关节增量/绝对位置解释 |
| `vla.execute_steps` | 5 | 每次预测 50 步后实际执行的前缀长度 |
| `vla.max_chunks` | 12 | 单次技能最大推理/执行块数 |
| `vla.action_hz` | 20 | 动作块内部执行频率 |
| `vla.max_joint_step` | 0.06 rad | 每个动作目标相对实际关节位置的限幅 |
| `vla.max_gripper_step` | 0.01 m | 每个夹爪关节单步目标变化上限 |
| `vla.num_inference_steps` | 10 | 模型流匹配采样步数 |
| `vla.startup_timeout_s` | 240 s | 权重加载预算，计入任务总预算 |
| `vla.inference_timeout_s` | 60 s | 每次预测预算 |
| `simulation.approach_clearance` | 0.10 m | IK 接近点相对目标的上方偏移 |
| `simulation.near_tolerance` | 0.04 m | 接近效果的几何验收阈值 |

VLA 在任务初始化阶段加载，整次任务复用模型；结束后释放进程与 GPU。等待推理时维持关节目标，尚未做异步动作流水线或推理延迟补偿，20 Hz 不是整个闭环持续有效频率。仿真运行倍率也会影响墙钟时间与物理时间的关系。

网页展示控制器切换、VLA 指令、推理耗时、动作块、模型预测目标与实际限幅后的关节目标。原始 32 维输出和输入图像文件引用保存在任务事件中；高层 Codex 日志与低层 VLA 日志分开。

## 首次真实实验结果

[查看实验任务](http://127.0.0.1:8081/?task=44aefdf56c474c999fe34d0a3b8a1622)：指令“先接近红色配料，再抓取并抬起它，保持持有”。使用结构化目标和两步预算隔离测试 MOVE_TO→GRASP；两次高层技能选择仍由 Codex 完成。

- MOVE_TO：IK 接近成功，near:red 通过观测确认。
- GRASP：12 次真实 π0.5 推理、12 个真实执行动作块，共 60 步；未确认持物，判定 failure。
- 单次 VLA 推理 176–824 ms，含输入处理与动作回传；不是通用性能基准。
- 60/60 步至少有一个关节触发变化限幅，执行轨迹不等于未限制的原始策略输出。
- 最终 red 仍在桌面，末端偏离目标；这一次有限时长、受限幅且未标定的实验不能用于声称 base 在所有配置下都不能抓取。
- 没有 IK 抓取回退、物体附着或伪造成功；取消/停止获得确认，控制锁释放。

结果：`workspace/pi05/validation.json`；任务快照 `task.json`；完整高低层事件 `events.jsonl`；录像引用保存在任务状态中。上述首次试验视频为 `workspace/sapien/media/sapien-be7053324a30.mp4`。

新增自动测试覆盖：必须先 MOVE_TO 才能启动 GRASP、模型返回晚于控制权撤销时禁止派发、直接关节/夹爪执行与限幅、非法动作拒绝。独立物理测试覆盖停止动作块后拒绝迟到块。

## 下一步

首先核对 checkpoint 的训练动作定义、相机与机器人状态范围，并用对齐数据做短动作回放；然后收集 Panda 局部抓取数据，从 base 微调。成功率评估要区分未限幅的模型输出、限幅执行轨迹和最终物理效果。接近使用 IK 的边界可以保持，后续替换微调 checkpoint 不需要恢复手写抓取轨迹。
