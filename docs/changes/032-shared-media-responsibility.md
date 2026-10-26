# 032：通用媒体处理不依赖验证模块

将原 verification.images 中的 paired_images、select_images 移到共享 media.py。
它们只生成图片引用标签、配对和按上下文预算选择图片，不做目标判断；状态 journal、
决策模型和 reviewer 都从媒体模块使用这些函数。删除原文件，不保留重复实现。

同时让 Agent 契约从公共 contracts 导入 Message，避免把工具模块误当作公共契约所有者。
图像标签、配对选择顺序和多模态描述保持不变。

验证：运行现有多模态、验证、模型传输及状态记录回归；确认 state 不再导入 verification，
图片前后比较、资源证据追溯与原模型响应保持兼容。
