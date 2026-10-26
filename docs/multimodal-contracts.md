# 多模态输入输出与空值

状态记录、执行控制、验证共享资源描述。能力模型负责解释资源内容，核心负责校验和保留
引用、类型、来源与时间。内置图像传输支持图片；点云、深度、张量等交给理解该模态的
tool、StateBackend 或 reviewer adapter。增加资源类型不需要修改 Agent 循环。

## 资源格式

`primitive_vla.media.Artifact` 的必要字段只有 `kind`、`uri`：

```json
{
  "kind": "point_cloud",
  "uri": "cloud-42.ply",
  "media_type": null,
  "metadata": {"frame": null, "unit": "m", "shape": [300, 3], "dtype": "float32"}
}
```

| 字段 | 语义 |
| --- | --- |
| kind | 非空类型名，例如 image、point_cloud、depth、tensor、audio；扩展可以定义新类型 |
| uri | 非空资源引用，由接收 adapter 解释和加载 |
| media_type | 可省略、为 null 或填写 MIME 类型 |
| metadata | 可省略、为 null 或为 JSON 对象；常用 frame、unit、shape、dtype，可扩展 |

frame/unit 等未提供时不猜测。metadata 是描述信息，不替代动作参数所需的几何校验。
资源不可用时将整个槽设为 null，例如 `{"cloud": null}`，不伪造 URI。
空字符串不是有效的类型化 URI。旧字符串路径仍可使用，新增 adapter 推荐显式类型。

大图片、点云和张量使用引用，避免把二进制或大数组塞进模型上下文。`data` 允许任意 JSON
值，适合小数组、标量或结构化结果，不接受 ndarray、bytes 或其他 Python 对象。
资源引用必须对使用者可访问；远程模型输出本机私有路径时需由 adapter 转存或提供下载地址。
核心不会自动为点云下载文件、推断坐标系或执行格式转换。

## 工具和观测

```python
from primitive_vla.media import Artifact
from primitive_vla.state.contracts import Observation
from primitive_vla.tools import ToolResult

result = ToolResult(
    data=None,
    observations=[Observation(
        id="rgbd-42", sensor=None, captured_at=None,
        artifacts={
            "rgb": Artifact(kind="image", uri="frame-42.png"),
            "cloud": Artifact(kind="point_cloud", uri="cloud-42.ply",
                              metadata={"frame": None, "unit": "m"}),
            "mask": None,
        },
        data={"camera_pose": None},
    )],
)
```

实际采集时间存在时填写 Unix 秒；示例没有采集时间，因此不能充当严格验收的新鲜证据。
一个 Observation 代表同一采集来源／时间的观测；异步相机和激光雷达应分别建 Observation。
已存在的 Observation ID 对应内容不可改写。

Observation 的通用字段承担定位与追溯职责，机器人专有数据放在 data，不增加一套
覆盖所有机器人的固定状态字段。可选字段如下：

| 字段 | 语义 |
| --- | --- |
| subject | 所观察的对象／设备引用，可为 null；设备身份不要只编码进 sensor 名字 |
| sensor | 观测流标识，可为 null；同一 subject、sensor 的新测量使旧测量失效 |
| frame | 观测的坐标系，可为 null；多坐标系资源在各自 Artifact.metadata 中声明 |
| metadata | adapter 扩展描述，可为 null；未知厂商字段应显式放在此处或 data 中 |
| lifetime | scene（默认）或 reference；前者是实时场景，后者是地图、标定等参考资料 |
| ttl_s | 从 captured_at 计算的可选有效期，正数或 null |

reference 不因运动自动失效，但新版本、自己的 TTL、会话恢复仍会使其不可作为当前依据。
captured_at 应填写来源可追溯的采集／资料形成时间，不能用读取地图的时间补造新鲜度。
一条观测内不要混合不同有效期的地图和实时定位，应分开记录并通过 evidence 关联。
依赖旧地图和新定位的判断继承实时测量时间，同时保留地图依赖检查。

模型的 observations 索引只发送对象、传感器、坐标系、有效性和资源类型等小型描述。
`state.query(kind="observations", subject="rover", sensor="map")` 可找回索引之外的资料，
再用 `state.read(id=...)` 分页读取 data、metadata 和完整资源。默认 query 仍返回 claims。
原始观测不必先转成 StateClaim；模型可以直接看反馈并决定下一步。

`ToolResult.artifacts` 用于一般模型产物，例如裁剪点云、预测深度和渲染预览。
这类输出不会自动成为带采集时间的物理观测。需要状态判断时附带 StateClaim，引用真实
原始观测；预测标记 basis=predicted，缺少部分信息标记 status=partial。

类型为 image 的资源自动投影到原 `images` 通道。depth 即使保存为 PNG，也不自动当作
自然图像；需要展示时另提供 image 类型的可视化资源。旧 images/before_images、时间关联
及前后图像比较继续有效。未填写可选资源元数据时不发送这些字段，显式填写 null 则保留。

工具输入继续使用 `ToolCall.arguments` 对象，由工具的 parameters 声明字段；不要把原始
点云塞进调用信封。可复用共享类型生成完整的输入 schema：

```python
from pydantic import Field
from primitive_vla.contracts import Message
from primitive_vla.media import Artifacts
from primitive_vla.tools import ToolDefinition

class LocateInput(Message):
    artifacts: Artifacts = Field(default_factory=dict)
    target: str | None = None

definition = ToolDefinition(
    name="geometry.locate", description="从图像或点云中定位目标",
    parameters=LocateInput.model_json_schema(),
    output_schema={"type": ["object", "array", "null"]},
)
```

adapter 内通过 `LocateInput.model_validate(arguments)` 得到 Artifact 对象。
完整生成的 schema 包含本地定义，直接传入 parameters；不单独抽取 properties 丢掉 `$defs`。
`output_schema` 校验 ToolResult.data，包括数组或 null；资源字段由通用 Artifact 契约校验。

## 状态能力后端

`StateRequest` 保留 subject、property、完整 evidence，并提供只读派生视图
`request.artifacts`，键形如 `rgbd-42:cloud`；值为 Artifact、旧字符串或 null。
该视图不自动加载文件。原始标量／数组数据仍可从 evidence 的 payload 读取。

`StateResult` 可组合返回 claims、artifacts、data，也可只返回其中一种：

```python
from primitive_vla.state.backends import StateResult
from primitive_vla.media import Artifact

result = StateResult(
    artifacts={"segmented_cloud": Artifact(kind="point_cloud", uri="cup.ply")},
    data={"confidence": None},
)
```

后端仍声明支持的属性契约。`state.estimate` 将资源保存在工具结果中，返回 attempts、
state_refs、input_evidence 和 result；下游传入该调用结果 ID 即可再次获得产物。
未产生 claim 时 state_refs 为空，不生成虚假的状态判断。仅有 null 资源槽且无 data/claim
不能返回 ok；未支持的模态可返回 unsupported，缺少观测导致无法判断可返回 unknown claim。

工具返回时间不等于观测采集时间。产物可以供后续模型分析，但其调用记录和预览图本身
没有新鲜物理证据资格；推断时保留原始观测依据。状态能力默认仅对 unsupported 使用显式
配置的 fallback。验证器仍通过 OutcomeContext 的结果／证据收到完整资源描述。

## 空值与状态语义

| 情况 | 表达与处理 |
| --- | --- |
| 未返回资源集合 | artifacts 省略或 null，内部归一为 {} |
| 指定资源不可用 | artifacts 某槽为 null，保留槽名 |
| 元数据缺失 | 省略或显式 null；显式 null 保留 |
| 结构化输出为空 | data=null 原样保留；不同于 false、0、空数组、空对象 |
| 状态完全未知 | status=unknown，value 省略或 null |
| 有部分状态信息 | status=partial，显式给非 null value 与 evidence，仍需通过属性 schema |
| 明确已知空值 | status=known、显式 value=null、有 evidence，且属性 schema 允许 null |

known/partial 省略 value 会被拒绝。已知 null 的含义必须由属性契约定义，例如检测器
明确确认“没有标签”；不能用 known null 表示模型没看懂。reason、ttl_s、sensor 和
captured_at 都可省略／为 null；标识、状态枚举、控制回执等必要信息不任意放宽。

原 position.v1 / detection.v1 保持完整语义。模型只能给部分坐标时可以注册单独的属性：

```yaml
state:
  schemas:
    pose_estimate.v1:
      type: object
      properties:
        coordinates:
          type: array
          items: {type: [number, "null"]}
          minItems: 3
          maxItems: 3
        frame: {type: [string, "null"]}
        unit: {enum: [m, mm, null]}
      required: [coordinates]
      additionalProperties: false
```

对应 `value={"coordinates": [0, null, 1], "frame": null}` 可作为 partial 记录。
nullable 表示 schema 允许空值；“是否掌握完整信息”由 adapter 按该属性语义报告，核心
不会从任意 JSON 中猜测。严格验证拒绝 partial、unknown、预测和不新鲜的证据依赖，
即使外层另一条 claim 宣称 known 也不会绕过这个检查。

导航二维位置可以另注册 `navigation.position2d.v1`，而不修改 position.v1 的三维含义：

```yaml
state:
  schemas:
    navigation.position2d.v1:
      type: object
      properties:
        xy:
          type: array
          items: {type: number}
          minItems: 2
          maxItems: 2
        frame: {type: [string, "null"]}
        covariance: {type: [array, "null"]}
      required: [xy, frame]
      additionalProperties: false
```

这里 covariance 是可选信息，缺失不等同于位置未知；具体工具需要坐标变换时仍须检查
frame 已知。属性 schema 的宽松程度由 adapter 契约决定，schema 通过不代表可以直接驱动机器人。
后端配置中的 supports 声明该属性名，返回 StateClaim 时运行时按上述 schema 校验。
触觉数组、巡检读数、关节状态也可以保留为原始 data，只有需要共享语义判断时才注册属性。

参见 [架构](architecture.md)、[工具接入](tools.md)、[验证策略](verification.md)。
