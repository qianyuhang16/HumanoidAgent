# VLM 接口与 DeepSeek / GPT

模型通信位于 `vlm/`。`planning/ModelPlanner` 负责共享提示词、上下文、框架函数定义和按需图像读取。
更换模型只需注入另一份 `VLM` 实现，不复制 Planning 提示词，也不修改 Agent 的事件循环。

```text
ContextBuilder → ModelPlanner → VLM.generate(request) → VLMResponse → Decision → Runtime
                                ↑
                       MemoryOrganizer 也使用此接口
```

## 模型接口

`VLMRequest` 包含调用方提供的 `instructions`、`text`、`functions` 与 `images`。
图像输入包含字节和媒体类型。`VLMResponse` 返回文本及解析后的函数调用。
模型适配器不导入 Planning、Memory、Runtime 或机器人 Provider，不决定业务动作顺序。

`planning/prompts.py` 是统一规划提示词；Memory 整理有独立的职责提示词，两者均与模型厂商无关。
`ModelPlanner` 要求一轮返回一个框架动作。Runtime 校验动作契约，Router 校验具体 Tool 参数。

## DeepSeek 使用

```bash
.venv/bin/python -m pip install -e '.[gui,vlm,test]'
```

```python
import os
from primitive_vla.planning import ModelPlanner
from primitive_vla.vlm.deepseek import DeepSeekVLM
from primitive_vla.memory.organizer import MemoryOrganizer
from primitive_vla.interfaces.web import create_app

vlm = DeepSeekVLM(os.environ["DEEPSEEK_API_KEY"], model="deepseek-flash")
planner = ModelPlanner(vlm, read_resource=memory.read_resource)
# 将 planner 注入已有 Agent，并由宿主装配机器人与工具。
# agent = Agent(planner, registry, memory, skills, resources)
organizer = MemoryOrganizer(memory, vlm, interval=300)
app = create_app(agent, robots=robot_profiles, organizer=organizer)
```

`memory`、`registry`、`skills`、`resources` 和 `robot_profiles` 来自应用自身装配，框架不提供假机器人。
工厂通过既有 `primitive-vla desktop --factory module:function` 接入桌面。
未配置模型的默认桌面入口仍提供草稿和历史查看。

`DeepSeekVLM` 使用官方 [`deepseek-flash` Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)，
默认请求超时 60 秒、输出上限 2048 tokens；非思考模式支持强制函数调用。
HTTP 可取消，无适配器内部重试。HTTP 错误和截断输出交给调用方；Planning 使用既有失败预算处理。

模型调用 `get_resource` 后，通用 ModelPlanner 读取该次明确请求的图片并交给 VLM 接口；
DeepSeek 适配器负责编码成服务支持的图片输入。不会扫描所有历史图片，不把资源 ID 当成视觉证据。
图片限制遵循 [DeepSeek Vision 文档](https://api-docs.deepseek.com/guides/vision/)。

密钥通过环境变量或被 Git 忽略、权限为 600 的本地配置传给构造器；不进入源码、日志、Memory 或提交。
对外发送的是调用方组装的任务上下文和明确读取的图像。

## 验证范围

`tests/test_vlm.py` 覆盖共享 Planning 提示词、通用文本调用、函数 Schema、图像字节、
错误反馈和暂停取消；`tests/test_module_boundaries.py` 检查模型实现不依赖 Agent 业务模块。
文件记忆及定时整理见 [Memory 设计](memory.md)。模型接入检查不是自主机器人任务的成功率评测。

## GPT 与独立配置

`vlm.gpt.GPTVLM` 使用 [OpenAI Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)，
将文本、图像字节及调用方函数定义转为请求，解析 function_call/output_text；不带 Planning 提示词或机器人逻辑。
图像使用 Base64 data URL，参见 [官方图像输入文档](https://developers.openai.com/api/docs/guides/images-vision)。
函数请求设置 `tool_choice=required`、`parallel_tool_calls=false`、`strict=false`，保持本框架参数 Schema 的可选字段语义，
参数仍由 Runtime 校验；格式依据 [官方函数调用文档](https://developers.openai.com/api/docs/guides/function-calling)。

```python
from primitive_vla.vlm.config import VLMConfig

vlm = VLMConfig(provider="gpt", model="gpt-6-astra", api_key_env="OPENAI_API_KEY").connect()
# 或 VLMConfig(provider="deepseek", model="deepseek-flash").connect()
planner = ModelPlanner(vlm, read_resource=memory.read_resource)
```

模型名应选择账号实际可用的型号。GPT 默认 API 地址为 `https://api.openai.com/v1`，
使用 `max_output_tokens` 控制输出预算，预算也可能被推理消耗。请求设置 `store=false`，各轮由框架组装上下文，
不在适配器内部创建服务端会话、重试或执行工具。HTTP 错误、截断输出、拒绝和无效函数 JSON 向调用方传播。

桌面入口已改为独立模型配置 + 两层能力装配，见 [装配说明](assembly.md)。本轮 GPT 验证使用 HTTP 替身，
覆盖图像、函数、纯文本、错误与取消；没有据此宣称已完成真实 GPT API 或自主机器人任务验收。
