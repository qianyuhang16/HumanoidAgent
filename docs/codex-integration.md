# Codex GPT-5.5 / medium 接入

> 高层 Codex 接口继续使用；当前默认动作后端已切换为 MOVE_TO（IK）+ π0.5。本文九步成功记录属于之前的脚本基线，不是 VLA 成功证据。见 [当前 VLA 实验](vla-control.md)。

## 启动与查看

已正式接入本机 Codex CLI，复用已有登录，不修改全局 Codex 配置。SAPIEN 配置使用 **GPT-5.5 / medium** 完成目标解析、每一步技能选择与最终审阅。

```bash
.venv/bin/python -m primitive_vla serve \
  --config configs/sapien.json --workspace workspace/sapien
```

打开 [仿真工作台](http://127.0.0.1:8081)。网页并排显示实际物理仿真相机和模型决策摘要。输入任务后点击“开始执行”；历史任务可查看最后一帧、完整录像、决策记录、事实来源和配置快照。

这里的本机 Codex 是本地客户端；GPT-5.5 推理发送到远端，Qwen3-VL 则使用本机 GPU 权重。登录方式与账号权限见 [官方认证说明](https://learn.chatgpt.com/docs/auth)。

## 配置

在 `configs/sapien.json` 或网页“配置管理”中修改：

```json
{
  "vlm": {
    "provider": "codex",
    "model": "gpt-5.5",
    "timeout_s": 120,
    "max_images": 4,
    "codex": {
      "executable": "/home/estar/.nvm/versions/node/v20.20.2/bin/codex",
      "reasoning_effort": "medium"
    }
  },
  "parser": {"type": "vlm"},
  "planner": {"type": "vlm"},
  "verification": {"mode": "audit"},
  "execution": {"observation_poll_s": 0.5},
  "task_defaults": {"timeout_s": 900}
}
```

`codex.executable` 可填 PATH 中的 `codex` 或完整路径。本机版本为 `0.149.0`。网页“保存并测试连接”使用同一个 provider，不需要把 Codex 填进 HTTP 地址。更换机器后先运行 `codex login status` 确认登录。

`audit` 保留物理观测结论并附加模型审阅，`enforce` 同时要求模型确认。选择 `http` provider 并配置地址与模型名可接回 Qwen；HTTP 专用参数不影响 Codex。各模块提示词、预算、技能源和仿真参数均保持独立配置。

## 数据流与文件职责

```text
用户目标 + 观测事实 + 相机图像 + SKILL.md
                    ↓
          providers/factory.py
                    ↓
     CodexClient → codex exec → 严格 JSON
                    ↓
    TaskParser / StructuredPlanner / TaskVerifier
                    ↓
    技能契约与执行器 → SAPIEN → 新观测
```

| 文件 | 职责 |
|---|---|
| `providers/base.py` | 统一模型接口与公开输入摘要 |
| `providers/factory.py` | 根据配置选择 HTTP 或 Codex |
| `providers/codex.py` | 异步 CLI 调用、图像、事件、超时和进程回收 |
| `providers/codex_schema.py` | 解析、规划、审阅的严格 JSON Schema 与领域格式转换 |
| `runtime/session.py` | 装配客户端，模型等待期间持续更新观测 |
| `web/static/` | 仿真画面、模型决策面板、参数设置与记录回看 |

技能契约、说明和动作程序继续独立存放于 `skills/**/SKILL.md`。Codex 只能通过结构化返回选择合法候选，实际轨迹与夹爪控制由执行器和动作后端完成。

## 调用与事件

每次决策使用独立临时目录，通过参数数组启动 `codex exec`：`--ignore-user-config --ephemeral --skip-git-repo-check --sandbox read-only --model gpt-5.5`，显式设置 `model_reasoning_effort="medium"`，禁用 shell tool，传入 `--output-schema` 和 `--image`。提示词和上下文从标准输入传入，结果从最终 JSON 文件读取。CLI 的非交互、事件流和结构化输出能力见 [官方非交互模式说明](https://learn.chatgpt.com/docs/non-interactive-mode)。

网页展示可公开的结构化结果与简短依据，不保存或展示内部思维链。每次调用有唯一 `request_id`，记录以下事件：

- `model_requested`：用途、模型、推理强度、图像源、事实与候选。
- `model_progress`：CLI 会话启动与开始处理状态。
- `model_response`：结构化目标、选择依据或验收结果，以及耗时和 token 用量。
- `model_failed` / `model_cancelled`：失败或取消状态；不持久化原始凭据及 CLI 诊断输出。

推理等待期间每 0.5 秒更新相机与事实，避免五秒有效期的物理事实过期。观测失败中止任务；模型不会替代传感器更新物理事实。取消与超时终止整组 Codex 子进程；每次调用完成后清理临时目录。没有隐藏的规则规划回退。

## 实际验证

2026-09-15，完整自然语言任务“把所有配料放入碗里，然后搅拌并放回工具”通过：

- [查看完整仿真与决策记录](http://127.0.0.1:8081/?task=4bb7146732e34feeab3058629253d90a)
- 9 次技能执行全部成功：三次抓取/入碗，抓取工具、搅拌、工具归位。
- 11 次真实 Codex 多模态调用：1 次目标解析、9 次闭环技能选择、1 次结果审阅。
- 3 个解析后的目标组均由 SAPIEN 观测确认，模型审阅也全部成功。
- 单次模型调用约 10.0–19.5 秒，含 CLI 启动；只是这一次运行的观测值。
- 浏览器检查：画面更新、模型标识、决策卡片、设置页正常，无 JavaScript 错误；手机宽度无横向溢出。

结果摘要：`workspace/sapien/codex-validation.json`；完整任务与事件保存在该工作区的 SQLite 数据库，录像与相机帧保存在 `workspace/sapien/media/`。

自动测试通过真实子进程模拟 CLI，覆盖目标解析→技能选择→审阅的完整运行时连接、图像参数、公开事件、过期事实刷新、异常 JSON、图像目录边界，以及取消/超时后进程回收。

这是 Franka Panda 与刚体配料的仿真样例，还未验证 G1 + Inspire 真机。每次 CLI 调用独立传入状态与技能上下文，具有额外 token 开销；不把一次成功当作通用任务成功率。原先两次可行性探测留存在 `workspace/research/codex55/`。
