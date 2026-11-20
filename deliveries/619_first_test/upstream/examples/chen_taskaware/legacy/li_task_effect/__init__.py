"""Li Jiajin v0.1.1 calculation core, with local finite/uncertainty corrections.

Original archive and local changes are documented in docs/verification/2026-11-14-team-handoff.md.
No fallback host objects or second executor are imported.
"""
from .judge import judge_case

__all__=['judge_case']
