# RoboCasa 接入

接入源码、Skill、配置示例和专属测试位于 `feat/robocasa` 分支；安装前先执行 `git switch feat/robocasa`。
RoboCasa 接入已移到 `workspace/robocasa/`。Agent 核心不再包含 providers 目录、平台枚举或固定 Skill 包。
平台通过配置中的 command 启动独立进程，复用 RPent 的环境客户端与动作原语；工具调用仍经过 Registry、Router、Hook 和资源互斥。
Skill 位于工作区的独立 Markdown 文件，由运行配置选择，不包含固定解题流程。

## 环境准备

Python 3.10，Linux，正常可访问的 NVIDIA GPU 与 EGL。启动配置 connection.command 选择已安装依赖的 Python 解释器。
仿真依赖与框架核心分开安装，固定源码版本见 [requirements.txt](../workspace/robocasa/requirements.txt)。
RPent 只使用能力模块，不安装其整包 Planner/CLI 或模型权重。

```bash
uv venv --python 3.10 .venv
uv pip install --python .venv/bin/python -e '.[gui,vlm,test]' -r workspace/robocasa/requirements.txt
git clone https://github.com/RLinf/RPent.git workspace/robocasa/deps/RPent
git -C workspace/robocasa/deps/RPent checkout b96cbc15d9ba73cf47dc63e3c2512d423bae5365
.venv/bin/robocasa-download-assets \
  --assets-path "$PWD/workspace/robocasa/assets" --no-macros --skip-existing -y
```

已安装的环境不需要重新下载。平台配置会设置子进程的 RPent 源码路径和 EGL 设置，桌面启动脚本不再硬编码这些依赖。
`pydantic-ai-slim` 仅用于上游工具 Schema 生成，不配置模型后端。

本机资产安装约 22.8 GiB，源码依赖约 1.1 GiB；Python 环境另计。2026-11-15 清理了下载压缩包、旧测试、日志、报告与缓存，
平台目录保留 RoboCasa 源码、资产与依赖，本机模型和启动配置单独位于 configs/。GPU 已实际在正常主机权限下验证为 RTX 5090，
受限沙箱中的 GPU 访问失败不能据此判定没有 GPU。

## 配置与启动

独立模型配置与平台内部装配见 [装配说明](assembly.md)。已有本机配置时不要覆盖密钥。

```bash
# 首次配置时：
cp configs/vlm.deepseek.example.json configs/vlm.json
cp configs/runtime.robocasa.example.json configs/runtime.json
chmod 600 configs/vlm.json configs/runtime.json
# 设置 DEEPSEEK_API_KEY 或在本机配置填写 api_key 后启动：
bash scripts/start-desktop.sh
```

启动配置选择 RoboCasa 程序和 profile ID。RoboCasa 内部读取 platform.json、profiles/ 和 skills/，
按 `open-drawer`、`prepare-coffee`、`navigate-kitchen` 选择组合包，向宿主报告能力。包可放在任意位置。
每个工具拥有自己的 skill.md：开抽屉和咖啡装配全部 12 个，导航装配 6 个。
名称、参数、资源和入口均从文件读取，runtime 中不再维护工具注册清单。
新增工具声明或运行配置只需新增文件；新算法的实现通过 skill.md 的 entrypoint 接入。

每次运行的数据存于启动配置中的 run_dir，平台日志位于其 `native/platform.log`。
已有仿真 episode 不能静默恢复：重建需明确设置 `options.new_episode: true`，旧 Session 仍不允许在新 episode 重放。
未结束调用存在时，甚至显式 new_episode 也会被拒绝。

## 实际验收范围

2026-11-15 已通过通用文件装配和真实本地子进程启动 NavigateKitchen：发现 12 个工具、3 份所选 Skill、4 个相机。
检查 RGB/深度资源导入、JPEG 预览只读且不写 Memory、8 步小幅底盘动作、并发资源冲突拒绝，
随后调用独立任务验证并读取证据，最后确认平台进程退出码为 0。
验证结论为 failure，表示这次接口检查没有完成导航任务；未调用模型 API，也不是自主任务成功率评测。
验收产物保存在 /tmp，workspace 中不保留测试目录。

此前还在真实模拟器中检查过 OpenDrawer、PrepareCoffee、取消、机械臂/夹爪和执行失败。
平台迁移后的核心自动化检查覆盖 stdio 断开时保留占用、取消确认、退订和工厂退出。

随后按每个工具一个 skill.md 修正装配：OpenDrawer 在真实 GPU 子进程中读取 12 份文件并注册 12 个同名工具，
验证采图、深度像素定位、越界/过期输入拒绝、小幅底盘动作、互斥、任务验证与正常退出。
工具契约全部来自文件，runtime/environment.py 和 backend.py 不再提供注册清单。

## 能力及语义

| 名称 | 说明 |
| --- | --- |
| `state.robocasa.observe` | 读取最近已完成仿真步的状态缓存；动作期间可读，附时间、步号和 episode ID |
| `state.robocasa.capture_image` | RGB PNG 及可选深度 NPY，保存为 Memory 资源引用 |
| `state.robocasa.localize_pixel` | 将指定相机像素按深度投影到世界坐标，检查观测步号；不做目标识别或可达性判断 |
| `execution.robocasa.move_to / move_delta` | 世界坐标目标或相对末端位移 |
| `execution.robocasa.rotate_pitch` | 腕部旋转原语，具体语义见 Tool Schema |
| `execution.robocasa.set_gripper / release` | 夹爪操作 |
| `execution.robocasa.scripted_grasp` | 局部抓取控制序列，不代表抓取效果已被验证 |
| `execution.robocasa.move_base / navigate_to` | 局部底盘控制；不提供全局路径规划、SLAM 或语义寻物 |
| `verification.robocasa.task` | 原生 `_check_success()` 及进度、独立 JSON 证据，注明仿真真值来源 |

RPent 的参数说明与 Schema 用于注册原语；本集成在调用入口约束步数、正距离阈值、底盘速度和夹爪取值，
内部直接调用已验证的处理器。新增准备与恢复知识可通过 Skill 扩展，无需修改 Agent。

## 生命周期与边界

- 环境创建、动作、相机采集与清理都由一个专用线程执行，保持 MuJoCo/EGL 线程归属。
  读取状态缓存不触碰 MuJoCo；显式 `capture_image` Tool 排在当前动作之后。
  桌面预览使用独立只读订阅，空闲时定时采集、动作期间在仿真步之间限频采集，目标 3 FPS，
  边长为 `min(image_size, 512)`。同相机的订阅共享采集，慢消费者只保留最新帧；退订后停止额外渲染。
  预览不推进仿真、不写入 Memory，采集异常不会把动作转为失败。
- 所有推进仿真的原语占用同一实例的 `simulation` 资源，避免底盘与手臂调用同时推进环境。
- 取消在步间检查。若当前同步仿真步尚未返回，则保留占用和 `stop_unconfirmed`；工作线程实际结束后才释放。
  该证明依赖本地同步模拟器，不可直接推广为真实机器人或远程控制服务的停止协议。
  在 Provider 入口被拒绝、尚未提交原生动作的调用可以直接确认停止；未知或恢复的调用不能据此确认。
- 同一进程的暂停/恢复保留物理状态；进程重启不恢复 MuJoCo 状态。旧调用不会重放，
  新建 episode 后旧 Session 的工具请求会被拒绝。当前没有物理状态检查点恢复。
- 原语返回 `ok`、动作结束和任务成功分开记录。原生谓词异常属于验证调用失败，不伪造 failure/success 结论。
  关联动作必须已结束且来自同一 episode、Session、Task。
- 当前核心仍允许 Planner 自行报告成功，详见此前用户故事中的成功证据约束缺口。
  评测应以独立 Verification 记录为准，不能用 Planner 的文字结论代替它。
