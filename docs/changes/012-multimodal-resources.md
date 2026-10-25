# 012：统一多模态资源契约

引入共享 Artifact：必要字段只有 kind、uri，可选 media_type 和 metadata 允许 null。
类型名称开放，图片、点云、深度、张量等使用同一描述方式；二进制保存在资源中。
Observation 和 ToolResult 使用该契约，继续接受旧路径字符串和空资源槽。
artifacts=null 归一为 {}；data 支持任意 JSON 值并保留 null、false、0 和空数组的区别。
Observation.sensor 可以省略或为 null，采集时间仍不由接收时间代替。

图片投影集中处理：图片进入原有模型图像通道，点云、深度等保留描述符，不误作图片。
state.read 返回资源描述，日志关联图片与原始观测，快照可无损保存多模态记录。
可选资源元数据未填写时不序列化；显式 null 保留。

验证：新增混合 RGB/点云/深度观测、空值、旧路径、读取、采集时间关联和快照回归测试。
不读取真实相机或点云文件；资源加载由各模型／工具 adapter 负责。
