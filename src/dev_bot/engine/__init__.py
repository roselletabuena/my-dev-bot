from __future__ import annotations

from .base import CodeEngine, EngineResult


def make_engine(name: str) -> CodeEngine:
    if name == "antigravity":
        from .antigravity import AntigravityEngine

        return AntigravityEngine()
    if name == "claude":
        from .claude import ClaudeEngine

        return ClaudeEngine()
    raise ValueError(f"unknown engine {name!r} (expected 'antigravity' or 'claude')")


__all__ = ["CodeEngine", "EngineResult", "make_engine"]
