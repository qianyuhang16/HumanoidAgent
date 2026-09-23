# 工具说明与旧技能文件

新 Agent 直接读取工具 description 与配置中的 instructions 文件。instructions 可以是普通 Markdown，描述能力、参数含义、真实入口要求、观察方法和使用经验；不要求 preconditions/effects 或固定执行顺序。

原有 SKILL.md 仍由 SceneTools 使用。其 YAML 包含技能名称、参数、前置条件、持续条件、效果和 program/policy 执行声明。SceneTools 把每个技能包装成可调用工具，将说明和参数 schema 提供给模型。

旧技能的执行检查留在该工具内部。effects 是其期望效果与控制停止条件的来源之一，不是所有 Agent 任务的完成条件。新能力无需先实现这套技能格式，直接提供工具即可。

接口与配置见 [tools.md](tools.md)。
