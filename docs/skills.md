# 文件化机器人技能

技能的契约、使用说明和仿真动作顺序都定义在 `SKILL.md` 中。Python 中的 `SkillSpec` 是共享数据类型；`simulation/motion.py` 提供通用运动算子，不再按 GRASP、PLACE、STIR 名称选择写死的流程。

## 技能目录

```text
src/primitive_vla/skills/
├── loader.py
├── kitchen/
│   ├── grasp/SKILL.md
│   ├── place/SKILL.md
│   ├── open/SKILL.md
│   ├── close/SKILL.md
│   ├── pour/SKILL.md
│   └── stir/SKILL.md
└── sapien/
    ├── grasp/SKILL.md
    ├── place/SKILL.md
    └── stir/SKILL.md
```

每个文件前部的 YAML 定义机器可读契约，后面的 Markdown 提供用途、限制和衔接说明。文件正文会进入 VLM 的解析与规划上下文，也显示在网页“技能契约”页。

## 选择与重新加载

部署配置通过目录选择技能：

```json
{"skill_sources": ["builtin:sapien"]}
```

`builtin:kitchen` 与 `builtin:sapien` 指向随包安装的技能库。也可以使用自定义目录或单个 `SKILL.md`；相对路径以配置文件所在目录为基准。例如 `configs/local.json` 中的 `"../my-skills"` 指向项目根目录的 `my-skills/`。目录会递归发现 `SKILL.md` 或 `skill.md`，重复的名称与版本会报错。

```bash
.venv/bin/python -m primitive_vla run --skills /path/to/my-skills
```

每次启动任务重新加载文件。网页的“重新加载 SKILL.md”按钮可立即更新技能预览。修改文件后无需重启网页服务；当前执行中的任务继续使用启动时的版本。

配置文件保存 `skill_sources`；SQLite 中的任务快照保存完整契约、Markdown 正文和动作程序，用于准确回看历史执行。API 返回的 `skills` 是加载后的视图。使用文件源时应编辑 Markdown；需要完全独立的内联快照时可显式设置 `skill_sources: []`，再提供 `skills` 列表，主要用于兼容旧配置。

## 一个技能文件的结构

下面是一个符号 mock 技能示例，可以保存为自定义目录中的 `reset/SKILL.md`：

```yaml
---
name: RESET_FLAG
version: "1"
description: 在健康状态下复位一个已声明的示例标志。
parameters: {}
preconditions:
  healthy: "true"
  event:demo: "true"
invariants:
  healthy: "true"
effects:
  event:demo: "false"
resources: [demo_channel]
backend: mock
timeout_s: 2
max_attempts: 1
---

# 复位示例标志

仅在用户明确要求复位时使用。执行后重新观察，确认 event:demo 为 false。
```

对应场景必须声明 `event:demo` 事实，任务目标设为该事实的最终值。已有规划器、注册表和 mock 后端可执行这个新名称，不需要增加 Python 技能分支。

`parameters` 声明可绑定对象类型，`{object}` 等占位符在调用时替换。条件使用字符串 `"true"`、`"false"` 或 `"unknown"`。其他字段见现有文件，包括 `resources`、`boundary`、`irreversible`、`capability`、`recovery` 与 `cancellation`。

字段分为执行契约和说明性元数据：

- `boundary` 当前只接受 `atomic`；未实现的 `local` 不再等到运行时才报错。
- `capability` 供规划上下文与展示使用，不是经过标定的能力分数。
- `recovery`、`cancellation` 是说明文本，不会被解释成恢复程序或取消规则。实际恢复沿用观察/重规划流程，停止由后端 `stop()` 实现。

mock 的故障注入适用范围也由技能文件的 `mock_faults` 列表声明，不按技能名称硬编码。

## 仿真动作程序

SAPIEN 技能在 YAML 中额外声明 `program`。例如抓取的部分步骤：

```yaml
program:
  - op: point
    kind: object_grasp
    object: "{object}"
    into: p
  - op: gripper
    opened: true
  - op: move
    point:
      ref: p
      height: {config: travel_height}
  - op: move
    point: {ref: p}
  - op: gripper
    opened: false
  - op: require
    condition: holding
    object: "{object}"
    reason: "No opposing finger contacts on {object}"
```

完整示例见 [抓取](../src/primitive_vla/skills/sapien/grasp/SKILL.md)、[放置](../src/primitive_vla/skills/sapien/place/SKILL.md)、[搅拌](../src/primitive_vla/skills/sapien/stir/SKILL.md)。

| 算子 | 用途 |
|---|---|
| `phase` | 更新执行阶段，供网页时间线显示 |
| `point` | 获取几何点或工具偏移，保存为命名变量 |
| `move` | 通过 IK 与关节驱动移动到几何点；支持指定高度和时间 |
| `gripper` | 张开或闭合夹爪，并等待驱动响应 |
| `wait` | 等待指定秒数 |
| `require` | 读取实际夹持或入碗条件，失败时终止当前技能 |
| `circle` | 按声明半径、深度、圈数与时间生成圆周轨迹，以观测角位移和接触验收 |

`{config: 字段名}` 引用当前仿真配置。`point` 的几何类型包括 `object_grasp`、`placement_tcp`、`tool_offset`、`circle_start`。搅拌程序在下降前保存工具偏移，随后通过 `offset_ref` 复用；`extra_turns` 限定补偿滑动的额外轨迹圈数，达到实际验收标准即结束。

调整顺序、组合已有算子或增加同类技能，只需修改 Markdown。新增机器人硬件能力或新的底层运动算子仍需后端代码实现；Markdown 不执行任意 Python。HTTP 后端由远端网关实现对应技能名称，mock 则按声明效果更新符号世界。

可选的规则任务模板另放在 `config/defaults/tasks.json`，也可用 `parser.templates` 覆盖。启用 `parser.type=vlm` 后，自然语言目标由模型解析，不依赖模板匹配。

## VLA 技能库

`builtin:sapien_vla` 定义 MOVE_TO、GRASP、PLACE、STIR。MOVE_TO 使用 approach program；其余技能的 program 为空，使用 `policy_prompt`，对象参数按同一 bindings 替换后送入 π0.5。policy_prompt 与非空 program 不能同时使用。near 条件确保操作前已接近目标；动作是否成功仍由 effects 验收。参见 [VLA 控制](vla-control.md)。

启用 SAPIEN VLA 后，`program` 中仅允许现有 approach 路径使用的 `point / move / phase` 操作及 `kind=approach`（或无 `kind`）。该约束在配置加载、网页配置提交及技能重载时结合控制器配置验证；执行后端不再逐次扫描程序。`policy_prompt` 与 `program` 仍互斥。
