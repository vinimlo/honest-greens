"""D7: a check of the user's own that breaks (a grader that is down)."""

from __future__ import annotations

from typing import Any


def no_card_number(ctx: Any) -> list[str]:
    raise RuntimeError("grader unavailable: connection refused")
