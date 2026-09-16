# π0.5 / LIBERO 仿真评测

这个目录直接评测低层 VLA，不调用高层 Codex 规划器。模型根据任务文字与双相机图像输出动作，LIBERO / MuJoCo 执行并判断成功。运行数据统一写入 `workspace/benchmarks/libero/`。

## 模型选择

- 模型：[lerobot/pi05_libero_base](https://huggingface.co/lerobot/pi05_libero_base)，固定 revision `a217bfd3b14673cf2ce597e69997ab21866438dd`。
- 对齐依据：[OpenPI 的 pi05_libero 训练配置](https://github.com/Physical-Intelligence/openpi/blob/main/src/openpi/training/config.py)及[官方评测程序](https://github.com/Physical-Intelligence/openpi/blob/main/examples/libero/main.py)。官方 LIBERO checkpoint 是从 π0.5 base 进行 LIBERO 微调得到的；本目录使用 LeRobot 发布的 LIBERO 版本，不能据此声称通用 `pi05_base` 在 LIBERO 上零样本成功。
- Hugging Face 当前模型卡与 processor 文件比较通用：配置写着 `chunk_size=50`，processor 没有附带归一化统计。这里显式采用官方 LIBERO 设置：**预测 10 步、每次执行前 5 步、禁用离散状态提示、使用 LIBERO 的动作分位数统计**。没有直接沿用缺少统计的 processor。
- 归一化统计来自[官方 pi05_libero checkpoint 资产](https://storage.googleapis.com/openpi-assets/checkpoints/pi05_libero/assets/physical-intelligence/libero/norm_stats.json)。模型完整严格加载，缺失或多余权重会终止运行；只恢复模型中实际共享的权重别名。

这里是匹配模型与仿真接口的演示评测，**不等价于完整官方成绩复现，也不能证明它能直接控制自建 SAPIEN 场景**。

## 目录职责

| 文件 | 职责 |
|---|---|
| `config.json` | 模型路径、环境路径、任务、种子、相机、动作时域、输出目录 |
| `run.py` | 仿真生命周期、初始化、闭环执行、真实成功判定、保存视频与日志 |
| `policy_worker.py` | 在独立 Python 环境加载 LeRobot 模型，完成预处理、推理、动作反归一化 |
| `report.html` | 展示逐回合视频、成功率、推理耗时、真实动作曲线 |
| `src/primitive_vla/vla/weights.py` | 与现有 SAPIEN 接口共享严格模型加载实现 |

## 本机运行

在项目根目录执行。模型与仿真使用已有的两个环境，不混装依赖。

```bash
# 首次下载权重（约 14 GB；已下载过的文件会复用缓存）
HF_HUB_DISABLE_XET=1 /home/estar/miniconda3/envs/pi/bin/python -c \
  "from huggingface_hub import snapshot_download; print(snapshot_download('lerobot/pi05_libero_base', revision='a217bfd3b14673cf2ce597e69997ab21866438dd'))"

mkdir -p workspace/benchmarks/libero/model
curl -L https://storage.googleapis.com/openpi-assets/checkpoints/pi05_libero/assets/physical-intelligence/libero/norm_stats.json \
  -o workspace/benchmarks/libero/model/norm_stats.json
curl -L https://storage.googleapis.com/big_vision/paligemma_tokenizer.model \
  -o workspace/benchmarks/libero/model/paligemma_tokenizer.model

/home/estar/miniconda3/envs/libero/bin/python benchmarks/libero/run.py \
  --config benchmarks/libero/config.json

.venv/bin/python -m http.server 8083 --bind 127.0.0.1 \
  --directory workspace/benchmarks/libero
```

打开 **http://127.0.0.1:8083/spatial-10/**。再次运行请修改配置中的 `output`，保留此前结果。更换 suite 时一并修改 `max_steps`：官方 spatial/object/goal/10/90 分别为 220/280/300/520/400。增加 `episodes_per_task` 可扩展评测；`initial_state_start` 决定使用的官方初始状态编号。

本机模型环境：`pi`（PyTorch / LeRobot），额外需要 `sentencepiece==0.2.1`。仿真环境：`libero`（robosuite 1.4.0、MuJoCo 3.8.1）。LIBERO 仓库路径由配置指定，其资产路径使用已有 `~/.libero/config.yaml`。初始化文件是官方仓库中的 NumPy 序列化文件，显式以 `torch.load(weights_only=False)` 读取以兼容 PyTorch 2.6+。

## 哪些是模型决定的

```text
官方任务文字 + 当前外部相机 + 当前腕部相机
    ↓ 旋转 180°，双线性缩放到 224×224，分词
π0.5 flow matching（10 次去噪）
    ↓ 10×7 连续动作，经 LIBERO 统计反归一化
执行前 5 步 → 再次观测 → 再次推理
    ↓
LIBERO OSC_POSE 控制器 → Panda → MuJoCo 物理仿真
    ↓
LIBERO 原始任务谓词判定成功
```

**模型决定**：末端三维平移增量、三维旋转增量及夹爪控制量。它输出连续动作，没有可供展示的文字思维链；网页展示的是可验证的模型输入、动作预测和执行结果。

**程序确定**：测试任务/初始化编号、相机变换、动作反归一化、执行步数、OSC 控制器、最大时长、成功谓词。这些由 benchmark 定义或 JSON 配置确定。没有抓取轨迹脚本、物体吸附、人工选择动作、失败后 IK 接管。

这份官方 LIBERO 配置设置 `discrete_state_input=False`，PI05 没有额外连续状态分支。因此当前评测中机器人状态只被记录，**不进入模型**；视觉闭环提供动作反馈。不要为了“补充信息”擅自给它添加 `Task: ..., State: ...` 提示，那会偏离该 checkpoint 的设置。

OSC_POSE 使用机器人动力学计算关节控制；这与现有 SAPIEN 的“MOVE_TO 用 IK、其余操作由实验性关节 VLA 执行”是两个独立实验。要将 LIBERO 模型接到自建场景，需要重新对齐相机、动作空间和任务分布。

## 结果与可复核证据

每个输出目录含 `config.json`、`results.json`、`policy.log`、`index.html`。每回合含：

- `rollout.mp4`：外部相机与腕部相机并排，20 FPS，与 20 Hz 控制频率一致；推理等待时间不计入视频时长。
- `observation-*.npz`：每次推理实际使用的双相机图像，以及记录的机器人状态。
- `trace.jsonl`：原始归一化动作、反归一化动作、每步提交的控制量、执行后末端/夹爪状态、奖励、成功谓词。
- `initial-state.npy`、`result.json`：初始化与回合结果。总表另存 BDDL 和初始化文件 SHA256。

默认方案是 **LIBERO-Spatial 10 个任务 × 每任务 1 回合，seed=7，初始状态编号 0**。成功率分母包括所有正常完成或超时失败的回合；执行异常会单独标记并终止剩余评测，不能算成完成了全套测试。没有根据结果挑选任务或重试失败回合。

这里使用的是本机较新的 MuJoCo 与 LeRobot PyTorch 实现，推理精度为 bfloat16；这些与官方原始评测环境可能不同。少量回合结果只能说明这次运行，不能用来推断完整 benchmark 成绩。

## 2026-09-15 实测结果

运行目录：[`workspace/benchmarks/libero/spatial-10`](../../workspace/benchmarks/libero/spatial-10)。

**9/10 成功（90%）**，实际完成 1,143 步控制、232 次模型推理。单次推理耗时中位数 **170.835 ms**，平均 174.148 ms；此处是 batch=1 的模型调用及输入处理耗时。十回合执行耗时合计 73.8 秒，不含权重下载、模型加载及环境创建。

| Task ID | 目标黑碗位置（均需放到盘子上） | 结果 | 控制步数 | 模型调用 |
|---|---|---|---:|---:|
| 0 | 盘子与小碗之间 | 成功 | 78 | 16 |
| 1 | 小碗旁边 | 成功 | 109 | 22 |
| 2 | 桌面中央 | 成功 | 115 | 23 |
| 3 | 饼干盒上 | 成功 | 89 | 18 |
| 4 | 木柜顶部抽屉内 | 失败 | 220 | 44 |
| 5 | 小碗上 | 成功 | 86 | 18 |
| 6 | 饼干盒旁边 | 成功 | 107 | 22 |
| 7 | 炉灶上 | 成功 | 122 | 25 |
| 8 | 盘子旁边 | 成功 | 104 | 21 |
| 9 | 木柜上 | 成功 | 113 | 23 |

失败回合达到 220 步上限，过程中没有程序异常。视频显示机械臂持续在抽屉附近调整，未将目标碗送到盘子上；仅凭该回合还不能把原因确定为视觉识别、接触控制或仿真版本差异。没有重试或删去这条失败记录。

已核对十回合逐步提交的控制量与模型动作块前缀一致、输入图像维度正确、视频帧数匹配、最终成功谓词与汇总一致；浏览器实际播放视频正常。证据：[结果总表](../../workspace/benchmarks/libero/spatial-10/results.json)、[核对摘要](../../workspace/benchmarks/libero/spatial-10/validation.json)、[模型来源与资产校验值](../../workspace/benchmarks/libero/model/provenance.json)。
