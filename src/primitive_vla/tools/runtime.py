"""Compatibility import; execution ownership lives in primitive_vla.execution."""

from primitive_vla.execution.runtime import ExecutionManager, validator

ToolRuntime = ExecutionManager

__all__ = ["ToolRuntime", "validator"]
