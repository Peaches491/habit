"""Data types for scoring a day's answers.

Scoring is a pure function of a config plus a day's *raw* answers (and any
recorded judge verdicts). Nothing here calls a model or touches storage; the
engine produces one explainable :class:`LineItem` per goal and the day total is
just their sum ("additive line items").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# A raw logged answer. bool for `bool` goals, int/float for `number`, str for
# `option`; judged goals may carry free-text. `None` means "not logged".
AnswerValue = bool | int | float | str


class ScoringError(Exception):
    """Raised for internal scoring faults that indicate a bug, not bad data."""


class Status(str, Enum):
    """Outcome of scoring a single goal for a day."""

    SCORED = "scored"                      # deterministically scored
    SKIPPED = "skipped"                    # no answer was logged
    PENDING_JUDGMENT = "pending_judgment"  # judged rule awaiting an agent verdict
    INVALID = "invalid"                    # the logged answer didn't fit the goal


@dataclass(frozen=True)
class JudgeVerdict:
    """An agent's decision on a judged goal, recorded into the raw log."""

    award: bool
    rationale: str = ""
    model: str = ""  # which model produced the verdict, for auditability


@dataclass(frozen=True)
class JudgeRequest:
    """Everything the agent needs to judge one goal's entry."""

    goal: str
    prompt: str            # the rule's natural-language judge prompt
    answer: AnswerValue    # the raw entry to judge
    max_points: int        # points at stake (full-or-zero)


@dataclass(frozen=True)
class LineItem:
    """The scored result for one goal on one day."""

    goal: str
    points: int
    status: Status
    detail: str                          # human-readable trace, for audit
    request: JudgeRequest | None = None  # set only when PENDING_JUDGMENT


@dataclass
class DayLog:
    """A day's raw inputs: answers, plus any judge verdicts already recorded.

    Both come from the raw (hand-editable) source of truth; the engine derives
    scores from them and never persists anything itself.
    """

    answers: dict[str, AnswerValue] = field(default_factory=dict)
    verdicts: dict[str, JudgeVerdict] = field(default_factory=dict)


@dataclass(frozen=True)
class DayScore:
    """All line items for a day, with derived totals and views."""

    items: list[LineItem]

    @property
    def total(self) -> int:
        """Sum of awarded points across all goals."""
        return sum(item.points for item in self.items)

    @property
    def pending(self) -> list[JudgeRequest]:
        """Judge requests for goals still awaiting an agent verdict."""
        return [item.request for item in self.items if item.request is not None]

    @property
    def by_goal(self) -> dict[str, LineItem]:
        """Line items indexed by goal name."""
        return {item.goal: item for item in self.items}
