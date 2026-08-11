"""Habit scoring: the rule engine.

Public API::

    from habit.scoring import score_day, DayLog

    day = DayLog(answers={"exercise": True, "water": 9})
    result = score_day(config, day)
    result.total          # summed points
    result.pending        # judge requests awaiting an agent verdict
    result.by_goal["water"].detail
"""

from __future__ import annotations

from .engine import score_day, score_goal
from .models import (
    AnswerValue,
    DayLog,
    DayScore,
    JudgeRequest,
    JudgeVerdict,
    LineItem,
    ScoringError,
    Status,
)

__all__ = [
    "AnswerValue",
    "DayLog",
    "DayScore",
    "JudgeRequest",
    "JudgeVerdict",
    "LineItem",
    "ScoringError",
    "Status",
    "score_day",
    "score_goal",
]
