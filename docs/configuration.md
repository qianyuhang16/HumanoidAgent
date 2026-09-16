# 配置说明

CLI 与网页使用同一个 `AppConfig`。配置文件允许部分覆盖；网页 GET `/api/config` 返回完整配置，PUT `/api/config` 一次性验证并保存。完整 JSON Schema 可从 `/api/config/schema` 获取。

配置源码分为 [`config/schema.py`](../src/primitive_vla/config/schema.py)（字段与校验）和 [`config/loader.py`](../src/primitive_vla/config/loader.py)（默认值、覆盖与保存）。内置场景与任务模板放在 `config/defaults/`；技能放在 `src/primitive_vla/skills/` 下各自的 `SKILL.md`，通过 `skill_sources` 选择。

部署示例为 [`configs/vlm.json`](../configs/vlm.json)；实际运行默认读取 `<workspace>/config.json`。`--config` 可指定独立文件，`--workspace` 决定任务数据库、上传图像与 CLI 默认导出目录。CLI 的 `--log-dir` 可单独覆盖导出位置。

## 配置分组

| 分组 | 字段 | 作用 |
|---|---|---|
| `name` | 字符串 | 网页工作区名称 |
| `parser` | `type`, `prompt` | `template` 示例解析或 `vlm` 自然语言解析 |
| `planner` | `type`, `max_nodes`, `history_limit`, `prompt` | 规则搜索上限、模型最近结果条数、规划提示词 |
| `vlm` | `provider`, `codex.executable`, `codex.reasoning_effort` | `http` 或 `codex`；CLI 路径与推理强度 |
| `vlm` | `base_url`, `model`, `api_key_env` | 兼容接口地址、服务模型 ID、密钥环境变量名 |
| `vlm` | `temperature`, `max_tokens`, `timeout_s`, `json_mode` | 请求参数；网络调用无隐藏自动重试 |
| `vlm` | `max_images`, `max_image_bytes`, `image_detail` | 多图像预算、每张本地图像字节上限、细节级别 |
| `vla` | checkpoint、norm_stats、action_mode、execute_steps、action_hz 等 | 可选 π0.5 低层控制；详见 [VLA 配置](vla-control.md) |
| `verification` | `mode`, `prompt` | `rules` / `audit` / `enforce` 的任务级验证 |
| `task_defaults` | `max_steps`, `timeout_s`, `max_observations` | 步骤、全任务时间和重新观察上限；模型不能扩大预算 |
| `execution` | `poll_s`, `stop_timeout_s`, `observation_poll_s` | 执行监控、停止确认超时、模型等待期间观测间隔（0 关闭） |
| `state` | `confidence_threshold`, `mock_fact_ttl_s` | 事实可信度阈值和 mock 非事件事实 TTL |
| `backends` | 名称 → 后端配置 | 一个或多个已注册执行器 |
| `observer_backend` | 已注册后端名 | Agent 的场景观察器；各后端应观察同一个物理世界 |
| `scene` | `objects`, `facts`, `images` | 对象表、初始三值事实、参考图像源 |
| `skill_sources` | 目录或文件列表 | `builtin:kitchen`、`builtin:sapien` 或配置文件相对路径，加载 SKILL.md |
| `skills` | 已加载 SkillSpec 列表 | API 预览和任务快照；使用文件源时不作为持久化技能来源 |
| `web` | `host`, `port`, `refresh_ms` | 网页监听与页面刷新间隔；host/port 需重启 |

同时包含 `scene` 和 `skills`（或非空 `skill_sources`）的文件视为完整配置，保留显式删除的对象/事实/后端。其余文件作为部分覆盖：技能列表整体替换，嵌套字典逐字段合并。未识别的配置字段会报错，避免拼写错误悄悄失效。

## 动作后端

每个 `backends.<name>` 可配置：

```json
{
  "type": "mock",
  "delay_s": 0.45,
  "fault": "none",
  "base_url": "http://127.0.0.1:9000",
  "api_key_env": "ROBOT_API_KEY",
  "request_timeout_s": 5,
  "poll_s": 0.1,
  "model_version": "remote-unreported"
}
```

`mock` 使用 `delay_s / fault`。故障可选 `none / missed_grasp / uncertain_pour / timeout`。HTTP 后端使用其余连接字段；机器人策略能力仍由对应 SkillSpec 限定。多个 mock 后端共享同一符号世界。

接真实网关时，把 `type` 改为 `http`、填入实际地址，并使技能 Markdown 中的 `backend` 与 `observer_backend` 指向正确名称。最简单的验证方式是将已有 `backends.mock` 的类型改为 `http`，名称只是注册别名；也可以新增明确的 `robot` 名称并修改技能绑定。

SAPIEN 后端使用 `type=sapien`，参数集中在 `simulation` 子配置；示例为 `configs/sapien.json`。观察与所有技能必须使用同一个仿真后端，确保它们读取和操作同一个物理世界。字段与启动方法见 [simulation.md](simulation.md)。

## 场景、图像与技能

`scene.objects` 的 `id` 必须稳定且唯一。`scene.facts` 声明任务可用谓词；未知事实显式写 `"unknown"`。真实后端忽略 mock 初始真值，以 `/observation` 的传感器事实为准。

`scene.images` 是 `source -> reference` 映射：

```json
{"front": "上传后返回的文件名.png", "wrist": "https://camera.example/frame.jpg"}
```

本地图像必须在工作区 `media/` 内，避免任意路径读入模型请求。HTTP provider 将 HTTP(S) 图像 URL 作为 image_url 输入，需模型服务能够访问；Codex provider 下载到每次调用的临时目录，校验大小和类型，调用结束清理。上传的文件不会覆盖，历史任务仍能引用原图。

SkillSpec 可编辑字段参见[SKILL.md 文件格式](skills.md)。技能在执行中只检查 `invariants`，正常效果验收只检查 `effects`；不要把 GRASP 的“手空闲”入口要求复制为持续条件。`max_attempts` 是整个任务内按技能版本和参数绑定累计的尝试上限；`irreversible=true` 会进一步限制为一次。

## 验证模式

| 模式 | 运行行为 | 最终成功条件 |
|---|---|---|
| rules | 事实与技能效果验证 | 所有目标有观测确认 |
| audit | 额外调用 VLM，记录逐目标结论 | 保留规则结果；VLM 错误/未知单独记录 |
| enforce | 额外调用 VLM，检查目标覆盖与证据引用 | 规则通过，且 VLM 对每个目标均确认成功 |

VLM 验证不会修改事实、重试技能或自动恢复不可逆动作。它仍是模型判断，不能替代物理传感器验证。

## 密钥与配置快照

环境变量在服务进程启动环境中读取。网页临时密钥接口只存于内存，刷新网页不丢失，重启服务后消失。配置文件仅保存技能源引用，任务快照另保存加载后的契约、说明与动作程序。保存配置、任务快照、模型调用事件时不保存密钥；模型事件记录用途、模型名、图像源、耗时、token 用量，以及决策输入事实、候选列表和结构化结果，不保存内部思维链。

技能 Markdown 与配置修改后，当前任务继续使用自己的快照，后续任务使用新配置；网页 POST `/api/skills/reload` 重新加载技能预览。重启不恢复内存去重账本，也不重新发送历史动作。

## Codex provider

`vlm.provider="codex"` 时使用 `vlm.model`（本机已验证 `gpt-5.5`）、`vlm.codex.executable`、`vlm.codex.reasoning_effort`、`vlm.timeout_s` 和图像预算。默认推理强度 `medium`，SAPIEN 配置单次调用超时 120 秒、任务总预算 900 秒。CLI 复用本机已有登录；不使用 `base_url`、API Key 输入框、temperature、max_tokens 或 HTTP JSON 模式。`image_detail` 仅用于 HTTP provider。

模型等待期间的 `execution.observation_poll_s` 与规划器主动“重新观察”不同：前者刷新相机和事实，不消耗 `max_observations`。观测失败会中止当前模型调用并报告错误；取消和超时会回收 Codex 子进程。

当前 SAPIEN 部署启用 `vla` 和 `builtin:sapien_vla`：MOVE_TO 的 program 负责 IK 接近，操作技能使用 policy_prompt。历史脚本基线需同时使用 `vla=null` 与 `builtin:sapien`，不自动回退。
