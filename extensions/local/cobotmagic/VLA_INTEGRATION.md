# LeRobot RTC VLA 接入调研

检查日期：2026-09-28。以下保留调研依据与设计取舍。工具接入已实现，最新使用约定见 [AGENT.md](AGENT.md) 与 [SKILL.md](skills/cobotmagic-manipulation/SKILL.md)。已做假模型/假机器人验证；尚未加载真实 GPU 权重或测试真机。

## 现有链路

实际仓库为 `/home/estar/qyh/agilex_lerobot`，不是 `agilex-lerobot`。

`inference_rtc.sh` → `lerobot.scripts.lerobot_rollout` → `build_rollout_context` → `BaseStrategy` → `RTCInferenceEngine` → `send_next_action` → `MagicBot.send_action` → 现有 ROS TCP 桥。

脚本参数：

| 项目 | 当前值 |
| --- | --- |
| checkpoint | `/home/estar/qyh/agilex_lerobot/checkpoints/pick_blue_cup_full/pretrained_model` |
| 模型 | π0.5；`use_peft=true`，LoRA + modules_to_save |
| 基座 | `lerobot/pi05_base` |
| 指令 | `Pick up the blue cup, pour into the bowl and place beside the bowl` |
| 机器人 | `magicbot`，`10.192.73.147:48055` |
| 控制频率 | 20 Hz |
| RTC | execution_horizon=13，max_guidance_weight=5，prefix_attention_schedule=EXP，queue_threshold=25 |
| 运行时长 | duration=0：无限运行 |
| 退出 | 不回初始位置 |
| 键盘 | Space 切换暂停/继续，Esc 退出；启动后自动进入控制循环，并非等待 Space 才开始 |

输入为 front/left/right 三路 RGB、14 维状态和文本任务。配置中的两个 empty_camera 是模型补位，不是缺少两个真实摄像头。图像由原预处理转为模型需要的尺寸；状态和动作使用 checkpoint 的 QUANTILES 归一化统计。

输出为 14 维绝对关节/夹爪动作：左臂 6 关节+夹爪，右臂 6 关节+夹爪。`use_relative_actions=false`；chunk_size=50，实际 RTC 消费/拼接由 ActionQueue 管理，不能把 n_action_steps=10 当作整个任务的长度。此模型不输出 TCP XYZ，也没有语义完成信号。

checkpoint 内记录 masterLeft/masterRight 的训练字段名，硬件使用 left_joint_1.pos 等名称；当前 `_resolve_action_key_order` 在名称集合不同而维度相同时回退硬件顺序。接入需离线断言训练顺序与硬件顺序对应，不按名称字母排序重新拼接。

## 权重与环境复用

- 适配器文件已存在，约 1.69 GB（十进制）。
- 本机 Hugging Face 缓存中 π0.5 基座 `model.safetensors` 已存在，约 14.47 GB（十进制）；缓存 revision 为 `b211f3d44c36b6acfcf7ae94a64e8e96f75a64ba`。
- 可复用 `/home/estar/miniconda3/envs/lerobot/bin/python`，editable 安装指向当前 `agilex_lerobot/src`。
- 已离线导入 PI05Policy、RTCInferenceEngine、MagicBot 和 build_rollout_context。环境元数据：torch 2.11.0，transformers 5.5.4，peft 0.21.0。
- 仓库 `.venv` 未发现 transformers/peft 的包元数据，不作为首选解释器。
- 保留现有 checkpoint、tokenizer、pre/postprocessor 及其统计文件；直接引用路径与缓存，不复制、合并另存模型或安装另一份环境。加载时使用离线模式，缺文件时明确报错。

这些检查不等于已验证 GPU 加载、显存余量、实时速度或真实动作质量。

## 推荐接口

当前动作工具为 `vla.run(policy_id, preparation_id, duration_s)`，训练任务文本来自策略配置。模型能力、默认训练指令、适用初始状态及执行预算来自配置中的策略目录。原调研允许 Agent 自选调用顺序；后续用户明确要求每次 VLA 前先执行 IK、换臂前复位，当前已通过扩展 SKILL 和工具凭据落实。普通问答不触发运动。

第一版以一次有限时间的执行作为一次工具调用：工具持有机器人资源，完成或取消后停止输出、清空队列，返回实际执行状态和末帧。若需要继续，由 Agent 观察后再次决定。每次调用重置 RTC 状态，因此过短的时间片会改变连续策略表现；执行时长应根据已有成功 rollout 确定，不能从脚本的 duration=0 推出合适值。

建议结果字段：`policy_id`、`stop_reason`、`elapsed_s`、`actions_sent`、`last_feedback`、`images`、`task_success`（未验证时为 null）。正常到时只是本轮执行结束，不宣称“倒水成功”；随后可用相机/VLM或其他验证工具判断。

取消沿用网页当前取消入口，通过动作工具的 `stop(call_id, reason)` 停止该调用。不要把 `vla.start()` 立即返回且后台继续运动接到当前运行时：当前资源租约以工具调用为周期，返回后会释放资源，IK 可能抢占。真正后台运行及 Agent 同时监测，需要另外实现跨调用执行租约，第一版不隐含提供。

IK 的 3 秒规定只作用于 IK 轨迹。VLA 保留训练/验证时的 20 Hz 执行节奏和独立的时长预算；不能把抓取、倾倒、放置整体压成 3 秒。

## 代码放置与生命周期

在本地扩展新增 `primitive_vla_cobotmagic/vla/`，分开工具适配、worker 协议和 LeRobot rollout 实现。所有重型 LeRobot/PyTorch 导入在已有 lerobot 环境的 worker 内完成，主 Agent 环境不安装这些依赖。

建议在现有 `configs/agent.yaml` 增加可选 `vla` 配置段，放置解释器、源码路径、策略目录、checkpoint、默认任务文本、执行时长与 RTC 参数。桥地址从既有 Cobot Magic 连接配置解析，避免维护两个相互矛盾的地址。具体可用字段以当前 configs/agent.yaml 为准。

复用的位置：

- `lerobot/rollout/context.py`：PEFT 基座+适配器加载、预后处理、特征顺序、RTC 装配。
- `lerobot/rollout/inference/rtc.py`：RTC 推理与 action queue。
- `lerobot/rollout/strategies/core.py::send_next_action`：动作反归一化后的映射及机器人端 processor。
- `lerobot/robots/magicbot/`：原桥协议、单位、限幅和夹爪转换。

新增一个可受程序控制的执行循环，替代 BaseStrategy 的键盘交互，不用模拟键盘，也不在 Agent 工具里拼 shell 启动无限运行脚本。不修改 ROS 桥，不重写 RTC 算法。

CobotTools 组合 VLA 工具并在 VLA 开始、结束或失败时作废其 observation/detections/last_plan/IK snapshot；与 robot.execute 使用相同的 `cobotmagic.robot` 和 `cobotmagic.snapshot` 资源。运行时资源锁不约束外部终端启动的 inference_rtc.sh，手动脚本与网页不能同时持有实际控制。

当前 TaskService 每条消息都会创建并关闭 Runtime；把大模型直接绑定单轮 provider 会每次追问都重新加载。推荐 worker 的权重缓存由网页工作区生命周期管理，懒加载；每轮工具只申请一次执行并在退出时释放，空闲不发送动作。终端模式可为单进程拥有 worker。模型驻留不代表保留旧 RTC 动作队列；下一次调用从当前观测重新初始化队列和预处理状态。

第一版也可使用单轮 worker 验证数据链路，但需要明确其重复加载开销，不能称为跨会话模型复用。

## 停止与错误语义

RTC `pause()` 只是清除事件，已经开始的一轮推理仍可能运行；`stop()` join 超时 3 秒后也不保证线程已退出。接入应先关闭唯一动作发送入口、等待在途发送结束，再处理推理队列和资源释放。未确认停止时不得把“进程退出”“TCP ack”或工具返回当作真机停止证明。

目前通用工具停止预算默认 2 秒；Cobot Magic 配置已扩展为 25 秒，与 worker 的有界停止过程匹配；不能直接套在可能 join 3 秒以上的 RTC 清理上。现有 JsonProcess 可参考其 JSON-lines 传输，但工作线程执行期间必须仍能处理取消请求，不能顺序读 stdin 后阻塞到 rollout 结束。

脚本的默认 MagicBot 限幅及发送约定原样复用。VLA 控制双臂和夹爪，是该策略输出的一部分；不要套用 IK“保持夹爪不动”的逻辑，也不能未经验证丢弃另一臂的输出。

## 实施与验证顺序

1. 配置解析、策略描述、14 维顺序和三路图像映射的离线检查。
2. FakeRobot + FakePolicy 跑通工具调用、结果、取消、两次调用间队列重置、VLA/IK 互斥、旧计划失效及会话结束清理。
3. 离线加载真实 checkpoint，用保存观测推理，只记录动作，不发送机器人；确认形状、单位、数值与原 rollout 一致。
4. 在用户发起的真实任务中测试执行。先复现已有成功初始布局与训练指令，再验证 Agent 是否能正确组合观察、可选 IK、VLA 与事后验证。

仍需实际验证：已有成功 rollout 的初始姿态/物体布局、正常任务时长、当前 GPU 显存余量、与视觉模型同时驻留的代价。IK 悬停点不是该 VLA 训练起始分布的已知证明，不能默认任何位置都可接上。
