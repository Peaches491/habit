"""Tests for the Habit config models and loader."""

from __future__ import annotations

import textwrap

import pytest

from habit.config import (
    Config,
    ConfigError,
    Goal,
    GoalType,
    JudgedRule,
    OptionsRule,
    ThresholdRule,
    load,
    loads,
)


def _yaml(text: str) -> str:
    """Dedent a triple-quoted YAML block."""
    return textwrap.dedent(text).strip() + "\n"


# --- Happy path --------------------------------------------------------------


def test_bool_goal_with_flat_int_value() -> None:
    cfg = loads(
        _yaml(
            """
            goals:
              - name: exercise
                description: Did you exercise today?
                type: bool
                value: 10
            """
        )
    )
    assert isinstance(cfg, Config)
    goal = cfg.by_name["exercise"]
    assert isinstance(goal, Goal)
    assert goal.type is GoalType.BOOL
    assert goal.value == 10
    assert goal.choices is None


def test_number_goal_with_threshold_rule() -> None:
    cfg = loads(
        _yaml(
            """
            goals:
              - name: water
                description: How many glasses of water?
                type: number
                value:
                  type: threshold
                  at_least: 8
                  points: 5
            """
        )
    )
    goal = cfg.by_name["water"]
    assert goal.type is GoalType.NUMBER
    assert isinstance(goal.value, ThresholdRule)
    assert goal.value.at_least == 8.0
    assert goal.value.points == 5


def test_option_goal_with_options_rule() -> None:
    cfg = loads(
        _yaml(
            """
            goals:
              - name: mood
                description: How was your mood?
                type: option
                choices: [great, ok, bad]
                value:
                  type: options
                  points_by_choice:
                    great: 5
                    ok: 2
                    bad: 0
            """
        )
    )
    goal = cfg.by_name["mood"]
    assert goal.type is GoalType.OPTION
    assert goal.choices == ["great", "ok", "bad"]
    assert isinstance(goal.value, OptionsRule)
    assert goal.value.points_by_choice == {"great": 5, "ok": 2, "bad": 0}


def test_judged_rule() -> None:
    cfg = loads(
        _yaml(
            """
            goals:
              - name: journaling
                description: What did you write about today?
                type: bool
                value:
                  type: judged
                  judge: Award if the entry shows genuine reflection.
                  points: 15
            """
        )
    )
    goal = cfg.by_name["journaling"]
    assert isinstance(goal.value, JudgedRule)
    assert goal.value.points == 15
    assert "reflection" in goal.value.judge


def test_metadata_and_indexing() -> None:
    cfg = loads(
        _yaml(
            """
            user: daniel
            timezone: America/Chicago
            goals:
              - name: exercise
                description: Did you exercise?
                type: bool
                value: 10
              - name: water
                description: Glasses of water?
                type: number
                value: 3
            """
        )
    )
    assert cfg.user == "daniel"
    assert cfg.timezone == "America/Chicago"
    assert set(cfg.by_name) == {"exercise", "water"}
    assert cfg.by_name["water"].value == 3


def test_metadata_is_optional() -> None:
    cfg = loads(
        _yaml(
            """
            goals:
              - name: exercise
                description: Did you exercise?
                type: bool
                value: 10
            """
        )
    )
    assert cfg.user is None
    assert cfg.timezone is None


# --- Name validation ---------------------------------------------------------


@pytest.mark.parametrize("name", ["has space", "tab\tname", "", "bad!", "a.b"])
def test_invalid_names_rejected(name: str) -> None:
    with pytest.raises(ConfigError, match="name"):
        loads(
            _yaml(
                f"""
                goals:
                  - name: "{name}"
                    description: whatever
                    type: bool
                    value: 1
                """
            )
        )


@pytest.mark.parametrize("name", ["exercise", "drink_water", "read-30", "GYM2"])
def test_valid_names_accepted(name: str) -> None:
    cfg = loads(
        _yaml(
            f"""
            goals:
              - name: {name}
                description: whatever
                type: bool
                value: 1
            """
        )
    )
    assert name in cfg.by_name


def test_blank_description_rejected() -> None:
    with pytest.raises(ConfigError, match="description"):
        loads(
            _yaml(
                """
                goals:
                  - name: exercise
                    description: "   "
                    type: bool
                    value: 1
                """
            )
        )


# --- choices consistency -----------------------------------------------------


def test_option_goal_requires_choices() -> None:
    with pytest.raises(ConfigError, match="choices"):
        loads(
            _yaml(
                """
                goals:
                  - name: mood
                    description: How was your mood?
                    type: option
                    value: 5
                """
            )
        )


def test_choices_forbidden_on_non_option_goal() -> None:
    with pytest.raises(ConfigError, match="only valid for option"):
        loads(
            _yaml(
                """
                goals:
                  - name: exercise
                    description: Did you exercise?
                    type: bool
                    choices: [a, b]
                    value: 1
                """
            )
        )


def test_duplicate_choices_rejected() -> None:
    with pytest.raises(ConfigError, match="duplicates"):
        loads(
            _yaml(
                """
                goals:
                  - name: mood
                    description: How was your mood?
                    type: option
                    choices: [ok, ok]
                    value: 1
                """
            )
        )


# --- rule / goal-type compatibility -----------------------------------------


def test_threshold_rule_requires_number_goal() -> None:
    with pytest.raises(ConfigError, match="threshold rule is only valid for number"):
        loads(
            _yaml(
                """
                goals:
                  - name: exercise
                    description: Did you exercise?
                    type: bool
                    value:
                      type: threshold
                      at_least: 1
                      points: 5
                """
            )
        )


def test_options_rule_requires_option_goal() -> None:
    with pytest.raises(ConfigError, match="options rule is only valid for option"):
        loads(
            _yaml(
                """
                goals:
                  - name: water
                    description: Glasses?
                    type: number
                    value:
                      type: options
                      points_by_choice: {a: 1}
                """
            )
        )


def test_options_rule_unknown_choice_rejected() -> None:
    with pytest.raises(ConfigError, match="unknown choices"):
        loads(
            _yaml(
                """
                goals:
                  - name: mood
                    description: Mood?
                    type: option
                    choices: [great, bad]
                    value:
                      type: options
                      points_by_choice:
                        great: 5
                        meh: 2
                """
            )
        )


def test_rule_without_type_rejected() -> None:
    with pytest.raises(ConfigError):
        loads(
            _yaml(
                """
                goals:
                  - name: water
                    description: Glasses?
                    type: number
                    value:
                      at_least: 8
                      points: 5
                """
            )
        )


# --- Config-level validation -------------------------------------------------


def test_duplicate_goal_names_rejected() -> None:
    with pytest.raises(ConfigError, match="duplicate goal names"):
        loads(
            _yaml(
                """
                goals:
                  - name: exercise
                    description: A
                    type: bool
                    value: 1
                  - name: exercise
                    description: B
                    type: bool
                    value: 2
                """
            )
        )


def test_empty_goals_rejected() -> None:
    with pytest.raises(ConfigError, match="at least one goal"):
        loads(
            _yaml(
                """
                goals: []
                """
            )
        )


def test_unknown_key_rejected() -> None:
    with pytest.raises(ConfigError):
        loads(
            _yaml(
                """
                goals:
                  - name: exercise
                    description: A
                    type: bool
                    value: 1
                    poinst: 10
                """
            )
        )


# --- Loader-level errors -----------------------------------------------------


def test_invalid_yaml_rejected() -> None:
    with pytest.raises(ConfigError, match="invalid YAML"):
        loads("goals: [unclosed")


def test_empty_config_rejected() -> None:
    with pytest.raises(ConfigError, match="empty"):
        loads("")


def test_non_mapping_root_rejected() -> None:
    with pytest.raises(ConfigError, match="must be a mapping"):
        loads("- just\n- a\n- list\n")


def test_load_from_file(tmp_path) -> None:
    path = tmp_path / "habit.yaml"
    path.write_text(
        _yaml(
            """
            goals:
              - name: exercise
                description: Did you exercise?
                type: bool
                value: 10
            """
        )
    )
    cfg = load(path)
    assert cfg.by_name["exercise"].value == 10


def test_load_missing_file() -> None:
    with pytest.raises(ConfigError, match="not found"):
        load("/no/such/habit.yaml")
