"""Typed config models for Habit.

A user authors a YAML file describing the goals they want to track; it parses
into these models. The parser validates *structure* only — scoring semantics
(how points actually accrue on a given day) live in the rule engine, not here.

Extending the schema: add a new rule variant as a class below and include it in
the ``Rule`` union. Everything else — validation, indexing, loading — flows from
the models automatically.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Base(BaseModel):
    # Reject unknown keys so typos in hand-written YAML surface immediately
    # rather than being silently ignored.
    model_config = ConfigDict(extra="forbid")


class GoalType(str, Enum):
    """The kind of answer a goal expects each day."""

    BOOL = "bool"       # yes / no
    NUMBER = "number"   # a numeric quantity
    OPTION = "option"   # one of a fixed set of choices


# --- Rules -------------------------------------------------------------------
# A goal's ``value`` is either a flat int (fixed points) or a Rule encoding
# richer scoring logic. Rules form a discriminated union keyed on ``type``.


class ThresholdRule(_Base):
    """Award points to a ``number`` goal when the logged value clears a bar."""

    type: Literal["threshold"] = "threshold"
    at_least: float
    points: int


class OptionsRule(_Base):
    """Award points to an ``option`` goal based on which choice was selected."""

    type: Literal["options"] = "options"
    points_by_choice: dict[str, int]


class JudgedRule(_Base):
    """Defer the award to the agent, which judges the entry against ``judge``.

    This is the "agent fallback" half of the hybrid scoring model: full points
    or zero, decided by the agent from the natural-language ``judge`` prompt.
    """

    type: Literal["judged"] = "judged"
    judge: str
    points: int


Rule = Annotated[
    Union[ThresholdRule, OptionsRule, JudgedRule],
    Field(discriminator="type"),
]
"""Any rule. A rule mapping in YAML must carry a ``type`` field."""


_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class Goal(_Base):
    """A single habit/goal a user is tracking."""

    name: str
    description: str
    type: GoalType
    choices: list[str] | None = None
    value: Union[int, Rule]

    @field_validator("name")
    @classmethod
    def _name_no_spaces(cls, v: str) -> str:
        if not _NAME_RE.fullmatch(v):
            raise ValueError(
                "must be non-empty with no spaces "
                "(allowed characters: letters, digits, '_', '-')"
            )
        return v

    @field_validator("description")
    @classmethod
    def _description_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v

    @model_validator(mode="after")
    def _check_consistency(self) -> Goal:
        # `choices` is required for, and exclusive to, option goals.
        if self.type is GoalType.OPTION:
            if not self.choices:
                raise ValueError("option goals require a non-empty `choices` list")
            if len(set(self.choices)) != len(self.choices):
                raise ValueError("`choices` must not contain duplicates")
        elif self.choices is not None:
            raise ValueError("`choices` is only valid for option goals")

        # Rule/goal-type compatibility.
        value = self.value
        if isinstance(value, ThresholdRule) and self.type is not GoalType.NUMBER:
            raise ValueError("a threshold rule is only valid for number goals")
        if isinstance(value, OptionsRule):
            if self.type is not GoalType.OPTION:
                raise ValueError("an options rule is only valid for option goals")
            unknown = set(value.points_by_choice) - set(self.choices or [])
            if unknown:
                raise ValueError(
                    "points_by_choice references unknown choices: "
                    f"{sorted(unknown)}"
                )
        return self


class Config(_Base):
    """Top-level parsed config: optional metadata plus the goals."""

    user: str | None = None
    timezone: str | None = None
    goals: list[Goal]

    @field_validator("goals")
    @classmethod
    def _goals_unique_non_empty(cls, goals: list[Goal]) -> list[Goal]:
        if not goals:
            raise ValueError("config must define at least one goal")
        seen: set[str] = set()
        dupes: set[str] = set()
        for goal in goals:
            if goal.name in seen:
                dupes.add(goal.name)
            seen.add(goal.name)
        if dupes:
            raise ValueError(f"duplicate goal names: {sorted(dupes)}")
        return goals

    @property
    def by_name(self) -> dict[str, Goal]:
        """Goals indexed by their unique ``name`` (the indexing key)."""
        return {goal.name: goal for goal in self.goals}
