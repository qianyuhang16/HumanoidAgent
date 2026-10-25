# 013：区分空值、不完整值与未知状态

StateClaim.value 改为 JSON 值，拒绝不可序列化对象；known 必须显式给 value 并提供证据。
显式 value=null 是否有效由注册的属性 JSON Schema 决定，不再由通用信封一律禁止。
省略 value 不等于已知 null。unknown 仍只携带 null；新增 partial 表示有部分信息，
其值仍需通过属性 schema，不绕过类型校验。reason 可省略或为 null。

保留原有 position.v1 / detection.v1 的完整语义；模型的缺失坐标、frame 等需要
注册声明 nullable/optional 的契约，并用 partial 表达不完整信息，不能补造单位或坐标系。
partial 及其派生 claim 不能通过严格目标验证；它也不会与完整值产生伪冲突。
主模型提示、通用 JSON Schema 和 Codex wire 同步支持 partial 与显式 null。

验证：属性级 nullable 校验、partial schema 校验、缺失与 null 区别、两种模型 wire
往返、partial 的直接及间接证据均无法认证目标完成。
