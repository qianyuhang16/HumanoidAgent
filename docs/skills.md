# 工具说明与指令文件

新 Agent 直接读取工具 description 与配置中的 instructions 文件。instructions 可以是普通 Markdown，描述能力、参数含义、真实入口要求、观察方法和使用经验；不要求 preconditions/effects 或固定执行顺序。

核心中的旧 `skills/` 目录及技能目录加载器已删除。它们原本定义厨房和 SAPIEN 的固定动作、前置条件和效果，不是通用 Agent 的必要模块。

扩展可以保留自己的 `SKILL.md`，例如 Cobotmagic 对 IK、VLA 和复位顺序的说明。把路径加入配置的 `instructions`，运行时按 UTF-8 文本读取即可。文件名没有特殊语义；框架不会把文件中的步骤编译成固定流程。

工具的参数与执行由工具接口定义；说明文件用于帮助模型理解用法。这两者不需要绑定成一套场景技能格式。

接口与配置见 [tools.md](tools.md)。
