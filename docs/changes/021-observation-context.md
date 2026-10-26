# 021：跨场景观测归属与无 claim 检索

Observation 增加可选 subject、frame、metadata：分别表示对象／设备归属、坐标系和
adapter 扩展信息，允许缺失／null。data 继续保存任意 JSON，多模态通过 artifacts 引用。
不添加导航／双臂／四足／人形专属字段；具体单位、维度及反馈意义由 adapter 契约负责。

state.query 新增 kind=observations 与 subject/sensor 筛选，原 claims 查询兼容。
ModelContext 提供有界的原始观测索引，不要求先生成 StateClaim；完整数据由 state.read
按需读取。大数组与扩展元数据不塞入索引。相同 sensor 按 subject 区分观测流。
旧快照观测按默认值比较身份，重复引用不会重写历史，也不会把旧证据刷新成实时观测。

验证覆盖 S02–S08 原始数据往返、可选／额外字段、上下文截断后检索、观测流区分及旧快照。
