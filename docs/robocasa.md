# RoboCasa 安装、启动与使用

本文件随 feat/robocasa 分支发布。根 README 是完整入口；本页补充环境、配置路径、运行语义与排查方法。main 不包含本平台的运行包、配置或测试。

## 安装环境

使用 Linux、Python 3.10、NVIDIA GPU 与可用 EGL/OpenGL 驱动；独立桌面另外需要 Node.js 22.12+ 和图形会话。先在正常主机终端运行 `nvidia-smi`，受限沙箱里看不到 GPU 不代表主机没有 GPU。

从仓库根目录执行。已有 Python 环境、资产或本机配置时不要覆盖：

```bash
git switch feat/robocasa
python3.10 -m venv .venv
.venv/bin/python -m pip install -e '.[gui,vlm,test]' -r workspace/robocasa/requirements.txt
mkdir -p workspace/robocasa/deps
git clone https://github.com/RLinf/RPent.git workspace/robocasa/deps/RPent
git -C workspace/robocasa/deps/RPent checkout b96cbc15d9ba73cf47dc63e3c2512d423bae5365
npm --prefix desktop ci
npm --prefix desktop run setup
.venv/bin/robocasa-download-assets \
  --assets-path "$PWD/workspace/robocasa/assets" --no-macros --skip-existing -y
```

使用 uv 创建的环境可能没有 pip；对应安装命令为：

```bash
uv pip install --python .venv/bin/python -e '.[gui,vlm,test]' -r workspace/robocasa/requirements.txt
```

requirements.txt 固定 RoboCasa `2692d8fc5fd86708a1b2028dcbce892ec418a9e7`、robosuite `97cfbde4b68d8ec43dad20cf4747297866a6ca2e` 和 pydantic-ai-slim；其余 Python 依赖为版本下界，不声称是全量可复现锁文件。RPent 仅使用能力模块，不安装其完整 Planner/CLI，也不下载 VLA 权重。

资产下载需要网络，本机历史安装约 22.8 GiB，源码依赖约 1.1 GiB，Python 环境和下载缓存另计。assets/、deps/ 不提交，由固定源码与下载命令准备。platform.json 自动添加 deps/RPent 导入路径、设置 MUJOCO_GL=egl，并声明 assets_path、gpu、image_size。

## 两份独立配置

首次配置模型：

```bash
cp -n configs/vlm.deepseek.example.json configs/vlm.json
chmod 600 configs/vlm.json
read -rsp 'DeepSeek API Key: ' DEEPSEEK_API_KEY
printf '\n'
export DEEPSEEK_API_KEY
```

GPT 使用 configs/vlm.gpt.example.json、账号可用的图像模型及 OPENAI_API_KEY。模型配置在平台包之外，启动脚本继承环境变量。模板不含真实 Key，真实 configs/*.json 被 Git 忽略。运行任务会向选定模型发送任务上下文与图片，使用账号 API 配额。

选择运行配置：

| profile | 环境任务 | Skill 数量 | 脱敏启动配置 |
| --- | --- | --- | --- |
| open-drawer | OpenDrawer | 12 | configs/runtime.robocasa.example.json |
| prepare-coffee | PrepareCoffee | 12 | configs/runtime.robocasa-prepare-coffee.example.json |
| navigate-kitchen | NavigateKitchen | 6 | configs/runtime.robocasa-navigate-kitchen.example.json |
| tabletop-grasp | 本地 TabletopGrasp | 12 | configs/runtime.robocasa-tabletop.example.json |
| two-vegetables-in-pan | PlaceVegetablesEvenly | 12 | configs/runtime.robocasa-two-vegetables.example.json |

这些配置只含相对路径、profile、运行目录和 new_episode，不含密钥或开发者绝对路径。connection.cwd 和 run_dir 相对于配置文件；含路径的 command 相对于 cwd。提供的 `../../.venv/bin/python` 指向仓库根虚拟环境。

需要不同解释器/GPU时，复制示例到被忽略的 configs/runtime.json，再修改 connection.command 或 options.gpu。profile.environment 已声明的 task/seed 不允许被 options 覆盖；要换场景或种子应新增/修改 profile。模型与平台配置结构见 [装配说明](assembly.md)。

## 启动桌面与选择任务

例如台面抓取：

```bash
bash scripts/start-desktop.sh configs/vlm.json configs/runtime.robocasa-tabletop.example.json
```

双食材入锅：

```bash
bash scripts/start-desktop.sh configs/vlm.json configs/runtime.robocasa-two-vegetables.example.json
```

其他场景将第二个参数替换为表中的配置路径。也可复制选中的示例到 configs/runtime.json 后执行 `bash scripts/start-desktop.sh`。当前启动时选择 profile，不支持运行中热切换。

打开后确认 Runtime 已连接，机器人名称与当前场景一致，再新建会话输入目标。没有配置模型/平台时，直接 `primitive-vla desktop` 只打开草稿工作台，不会执行仿真任务。

建议输入：

- tabletop-grasp：“抓起台面上的柠檬，抬高至少 8 厘米并保持夹持，验证是否成功。”
- two-vegetables-in-pan：“把台面上的两种蔬菜都放进平底锅，分开放置，不要堆叠，完成后验证。”
- navigate-kitchen：“先观察环境，再导航到任务目标；不确定时说明缺少什么信息。”

这些是用户目标，不是保证成功的专家解法。目标位置、食材、视角及步骤由模型根据真实观察判断，不预设抓取像素、导航路径或恢复序列。

对话模式用于交流；调用详情查看实际参数、结果、错误和验证。运行中要补充指令时先暂停决策，再在原会话发送消息；预算累积不重置。停止动作需要等待停止确认。右侧视角只是桌面预览，正式采图工具才把画面提供给模型。

## 图片和像素定位

capture_image 直接附上 RGB 图片。模型只看到 image_1 等稳定引用、相机名称和尺寸；可选深度保留为资源。场景、相机、帧号保存在资源私有 metadata。

```json
{"image":"image_1","row":120,"column":86}
```

这是 localize_pixel 的参数示例，普通看图不需要行列。row/column 为左上角起始、从 0 开始的行列索引。Runtime 把图片引用解析为资源 ID，平台检查所属 episode、实际尺寸与当前仿真步，再进行深度回投。帧过期要重新采图，不能将旧图的像素投到新状态。

observe 只返回 robot_state 与 simulation_steps；运动返回原语结果，Verification 返回独立结论与依据。执行完成不代表抓取/放置成功。

## 场景与能力边界

TabletopGrasp 在 runtime/tasks.py 中定义台面和柠檬布局，成功判据为双指抓取接触且较初始高度抬升至少 8 cm；没有动作脚本。

PlaceVegetablesEvenly 使用上游场景。成功判据要求两个物体在锅内、中心高度差小于 2 cm、水平距离大于 6 cm，且夹爪远离物体。不要求点火或烹饪，食材种类由场景生成。

平台提供 state.observe/capture_image/localize_pixel、末端移动、夹爪、局部抓取、底盘移动、navigate_to 和 verification.task。完整名称与参数以 skills/ 中的 12 份文件为准。navigate_to 不提供 SLAM 或全局避障；scripted_grasp 是局部动作原语，不是高层任务策略。

## 数据、重启和排查

运行数据位于配置的 run_dir，包含 journal、sessions、资源与 native/ 平台数据；平台日志为 `run_dir/native/platform.log`。不要上传其中的任务、图片或 API 上下文。

- 首次 open-drawer 示例使用 new_episode=false，可创建首次 episode。已有场景不能静默重建，应换一个运行目录，或在本机配置明确设置 new_episode=true。
- 其他四份示例使用 new_episode=true，每次进程重启创建新物理场景。旧会话仅供历史查看，应创建新会话，不在新场景重放旧动作。
- 未结束调用或停止未确认时，即使 new_episode=true 也不能绕过资源占用。先在原进程完成停止确认，不能删日志伪装停止。
- 找不到 RPent：检查 platform.json 中的 deps/RPent 与固定检出版本。找不到 robocasa/robosuite：确认依赖装在 connection.command 指定的解释器里。
- EGL/GPU 初始化失败：先检查正常主机终端的 nvidia-smi、图形驱动/EGL、options.gpu；不要在 Agent 核心增加相机或硬件回退。
- 桌面打不开：检查 Node 版本、npm ci/setup 是否完成及 DISPLAY/图形会话；详见 [桌面文档](desktop.md)。
- 真实模型无法看图：确认模型支持图像输入，检查正式采图调用与模型附件链路，不以桌面预览代替证据。

## 验证

从仓库根目录：

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -t . -v
PRIMITIVE_PYTHON="$PWD/.venv/bin/python" npm --prefix desktop test
```

本次 156 项 Python 和 18 项桌面测试通过，涵盖短引用重载/淘汰、错误引用拒绝、像素坐标来源和过期帧、资源互斥、取消确认及平台进程接口。测试采用替身，不是新版真实模型或 GPU 完整任务成绩。

此前真实 GPU 检查覆盖采图、预览、小幅动作、独立验证和正常退出，但当时的工具接口版本不同。本文的安装步骤依据现有依赖声明整理，本次发布没有重新下载资产或从空环境完整安装。合并接口时先读 [本次更新与合并说明](updates/2026-11-20.md)。
