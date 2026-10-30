"""A workspace operation outcome, independent of its display text."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolResult:
    success: bool
    output: str
