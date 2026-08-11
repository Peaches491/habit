"""The rule engine: turn raw answers into awarded points.

Pure and deterministic. The LLM is never in this path — a ``judged`` rule emits
a :class:`JudgeRequest` for the agent and, once a :class:`JudgeVerdict` is
supplied, applies it. Re-running with the same inputs always yields the same
result, which is what makes recompute-on-read safe.

Flat-int scoring semantics (``value`` is a bare int = award N points if the
goal was "done"):

* ``bool``   — N if the answer is ``True``.
* ``number`` — N if the answer is ``> 0``.
* ``option`` — N if any valid choice was selected (participation).
"""

from __future__ import annotations

from ..config import (
    Config,
    Goal,
    GoalType,
    JudgedRule,
    OptionsRule,
    ThresholdRule,
)
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


class _Invalid(Exception):
    """Internal: the logged answer doesn't fit the goal. Becomes INVALID."""


def score_day(config: Config, day: DayLog) -> DayScore:
    """Score every goal in ``config`` against a day's answers and verdicts."""
    items = [
        score_goal(goal, day.answers.get(goal.name), day.verdicts.get(goal.name))
        for goal in config.goals
    ]
    return DayScore(items=items)


def score_goal(
    goal: Goal,
    answer: AnswerValue | None = None,
    verdict: JudgeVerdict | None = None,
) -> LineItem:
    """Score a single goal. ``answer=None`` means nothing was logged."""
    value = goal.value

    if isinstance(value, JudgedRule):
        return _score_judged(goal, value, answer, verdict)

    if answer is None:
        return LineItem(goal.name, 0, Status.SKIPPED, "no answer logged")

    try:
        points, detail = _score_deterministic(goal, value, answer)
    except _Invalid as exc:
        return LineItem(goal.name, 0, Status.INVALID, str(exc))
    return LineItem(goal.name, points, Status.SCORED, detail)


def _score_judged(
    goal: Goal,
    rule: JudgedRule,
    answer: AnswerValue | None,
    verdict: JudgeVerdict | None,
) -> LineItem:
    if answer is None:
        return LineItem(goal.name, 0, Status.SKIPPED, "no answer logged")

    if verdict is None:
        request = JudgeRequest(goal.name, rule.judge, answer, rule.points)
        return LineItem(
            goal.name, 0, Status.PENDING_JUDGMENT, "awaiting judgment", request=request
        )

    points = rule.points if verdict.award else 0
    word = "award" if verdict.award else "no award"
    model = f" [{verdict.model}]" if verdict.model else ""
    detail = f"judged {word}{model}"
    if verdict.rationale:
        detail += f": {verdict.rationale}"
    return LineItem(goal.name, points, Status.SCORED, detail)


def _score_deterministic(
    goal: Goal, value: object, answer: AnswerValue
) -> tuple[int, str]:
    """Return (points, detail) for a non-judged goal, or raise ``_Invalid``."""
    if goal.type is GoalType.BOOL:
        if not isinstance(answer, bool):
            raise _Invalid(f"expected true/false, got {answer!r}")
        points = value if answer else 0  # value is int for bool goals
        return points, f"{'yes' if answer else 'no'} -> {points} pts"

    if goal.type is GoalType.NUMBER:
        # bool is a subclass of int; a yes/no is not a valid number.
        if isinstance(answer, bool) or not isinstance(answer, (int, float)):
            raise _Invalid(f"expected a number, got {answer!r}")
        if isinstance(value, ThresholdRule):
            met = answer >= value.at_least
            points = value.points if met else 0
            comparator = ">=" if met else "<"
            return points, f"{answer} {comparator} {value.at_least} -> {points} pts"
        points = value if answer > 0 else 0  # value is int
        return points, f"{answer} -> {points} pts"

    if goal.type is GoalType.OPTION:
        if not isinstance(answer, str):
            raise _Invalid(f"expected one of {goal.choices}, got {answer!r}")
        if answer not in (goal.choices or []):
            raise _Invalid(f"{answer!r} is not one of {goal.choices}")
        if isinstance(value, OptionsRule):
            points = value.points_by_choice.get(answer, 0)
            return points, f"{answer!r} -> {points} pts"
        points = value  # value is int (participation)
        return points, f"{answer!r} -> {points} pts"

    raise ScoringError(f"unhandled goal type: {goal.type}")  # defensive
