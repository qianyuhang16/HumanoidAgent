"""错误边界：格式/绑定不合法 -> 拒配，不产生 Verification。"""

from __future__ import annotations


class TaskEffectError(Exception):
    """本模块的错误基类。"""


class EvidenceBindingError(TaskEffectError):
    """输入格式、版本、身份、坐标或资源引用不合法，应当拒配。

    与「证据合法但不可判」严格区分：后者由 judge_case 返回
    conclusion=uncertain 的正式结论，不会抛这个异常。
    """

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail

    def as_dict(self) -> dict:
        return {"code": self.code, "detail": self.detail}
