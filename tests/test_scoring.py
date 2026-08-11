"""Tests for the scoring rule engine."""

from __future__ import annotations

import textwrap

import pytest

from habit.config import (
    Goal,
    GoalType,
    JudgedRule,
    OptionsRule,
    ThresholdRule,
    loads,
)
from habit.scoring import (
    DayLog,
    JudgeVerdict,
    Status,
    score_day,
    score_goal,
)


# --- helpers -----------------------------------------------------------------


def bool_goal(name: str = "exercise", value=10) -> Goal:
    return Goal(name=name, description="d", type=GoalType.BOOL, value=value)


def number_goal(name: str = "water", value=5) -> Goal:
    return Goal(name=name, description="d", type=GoalType.NUMBER, value=value)


def option_goal(name: str = "mood", choices=("great", "ok", "bad"), value=3) -> Goal:
    return Goal(
        name=name,
        description="d",
        type=GoalType.OPTION,
        choices=list(choices),
        value=value,
    )


# --- bool goals --------------------------------------------------------------


def test_bool_true_awards_points() -> None:
    item = score_goal(bool_goal(value=10), True)
    assert item.status is Status.SCORED
    assert item.points == 10
    assert "yes" in item.detail


def test_bool_false_awards_zero() -> None:
    item = score_goal(bool_goal(value=10), False)
    assert item.status is Status.SCORED
    assert item.points == 0


def test_bool_wrong_type_is_invalid() -> None:
    item = score_goal(bool_goal(), "sometimes")
    assert item.status is Status.INVALID
    assert item.points == 0


# --- number goals: flat int --------------------------------------------------


def test_number_flat_positive_awards_points() -> None:
    item = score_goal(number_goal(value=5), 3)
    assert item.status is Status.SCORED
    assert item.points == 5


def test_number_flat_zero_awards_zero() -> None:
    item = score_goal(number_goal(value=5), 0)
    assert item.points == 0
    assert item.status is Status.SCORED


def test_number_rejects_bool_answer() -> None:
    # bool is a subclass of int, but a yes/no is not a number.
    item = score_goal(number_goal(), True)
    assert item.status is Status.INVALID


def test_number_rejects_non_number() -> None:
    item = score_goal(number_goal(), "lots")
    assert item.status is Status.INVALID


# --- number goals: threshold rule -------------------------------------------


def test_threshold_met_at_boundary() -> None:
    goal = Goal(
        name="water",
        description="d",
        type=GoalType.NUMBER,
        value=ThresholdRule(at_least=8, points=5),
    )
    item = score_goal(goal, 8)
    assert item.points == 5
    assert ">=" in item.detail


def test_threshold_not_met() -> None:
    goal = Goal(
        name="water",
        description="d",
        type=GoalType.NUMBER,
        value=ThresholdRule(at_least=8, points=5),
    )
    item = score_goal(goal, 7)
    assert item.points == 0
    assert "<" in item.detail


# --- option goals ------------------------------------------------------------


def test_option_flat_participation_points() -> None:
    item = score_goal(option_goal(value=3), "ok")
    assert item.points == 3
    assert item.status is Status.SCORED


def test_option_rule_maps_choice_to_points() -> None:
    goal = Goal(
        name="mood",
        description="d",
        type=GoalType.OPTION,
        choices=["great", "ok", "bad"],
        value=OptionsRule(points_by_choice={"great": 5, "ok": 2, "bad": 0}),
    )
    assert score_goal(goal, "great").points == 5
    assert score_goal(goal, "ok").points == 2
    assert score_goal(goal, "bad").points == 0


def test_option_rule_unmapped_choice_defaults_zero() -> None:
    goal = Goal(
        name="mood",
        description="d",
        type=GoalType.OPTION,
        choices=["great", "ok", "bad"],
        value=OptionsRule(points_by_choice={"great": 5}),
    )
    assert score_goal(goal, "ok").points == 0


def test_option_invalid_choice() -> None:
    item = score_goal(option_goal(), "meh")
    assert item.status is Status.INVALID


def test_option_non_string_invalid() -> None:
    item = score_goal(option_goal(), 3)
    assert item.status is Status.INVALID


# --- judged rules ------------------------------------------------------------


def judged_goal(name: str = "journal", points=15) -> Goal:
    return Goal(
        name=name,
        description="d",
        type=GoalType.BOOL,
        value=JudgedRule(judge="genuine reflection?", points=points),
    )


def test_judged_without_verdict_is_pending() -> None:
    item = score_goal(judged_goal(points=15), "Reflected on the week.")
    assert item.status is Status.PENDING_JUDGMENT
    assert item.points == 0
    assert item.request is not None
    assert item.request.goal == "journal"
    assert item.request.max_points == 15
    assert item.request.answer == "Reflected on the week."


def test_judged_award_verdict() -> None:
    verdict = JudgeVerdict(award=True, rationale="thoughtful", model="claude")
    item = score_goal(judged_goal(points=15), "entry", verdict)
    assert item.status is Status.SCORED
    assert item.points == 15
    assert "claude" in item.detail
    assert "thoughtful" in item.detail


def test_judged_no_award_verdict() -> None:
    verdict = JudgeVerdict(award=False)
    item = score_goal(judged_goal(points=15), "meh", verdict)
    assert item.status is Status.SCORED
    assert item.points == 0


def test_judged_missing_answer_is_skipped() -> None:
    item = score_goal(judged_goal(), None)
    assert item.status is Status.SKIPPED
    assert item.request is None


# --- missing answers ---------------------------------------------------------


def test_missing_answer_is_skipped() -> None:
    item = score_goal(bool_goal(), None)
    assert item.status is Status.SKIPPED
    assert item.points == 0


# --- day-level aggregation ---------------------------------------------------


def _config():
    return loads(
        textwrap.dedent(
            """
            goals:
              - name: exercise
                description: Did you exercise?
                type: bool
                value: 10
              - name: water
                description: Glasses?
                type: number
                value:
                  type: threshold
                  at_least: 8
                  points: 5
              - name: mood
                description: Mood?
                type: option
                choices: [great, ok, bad]
                value:
                  type: options
                  points_by_choice: {great: 5, ok: 2, bad: 0}
              - name: journal
                description: Reflection?
                type: bool
                value:
                  type: judged
                  judge: genuine reflection?
                  points: 15
            """
        )
    )


def test_score_day_sums_additive_line_items() -> None:
    cfg = _config()
    day = DayLog(
        answers={"exercise": True, "water": 9, "mood": "great", "journal": "wrote a lot"},
        verdicts={"journal": JudgeVerdict(award=True, model="claude")},
    )
    result = score_day(cfg, day)
    # 10 + 5 + 5 + 15
    assert result.total == 35
    assert set(result.by_goal) == {"exercise", "water", "mood", "journal"}
    assert result.pending == []


def test_score_day_surfaces_pending_judgment() -> None:
    cfg = _config()
    day = DayLog(answers={"exercise": True, "journal": "wrote a lot"})
    result = score_day(cfg, day)
    # journal is pending (no verdict); mood/water skipped
    assert result.by_goal["journal"].status is Status.PENDING_JUDGMENT
    assert len(result.pending) == 1
    assert result.pending[0].goal == "journal"
    assert result.by_goal["water"].status is Status.SKIPPED
    assert result.total == 10  # only exercise scored


def test_score_day_is_pure() -> None:
    cfg = _config()
    day = DayLog(answers={"exercise": True, "water": 9})
    first = score_day(cfg, day)
    second = score_day(cfg, day)
    assert first.total == second.total
    assert [i.points for i in first.items] == [i.points for i in second.items]
