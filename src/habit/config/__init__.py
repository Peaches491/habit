"""Habit config: models and YAML loader.

Public API::

    from habit.config import load, loads, Config, Goal, GoalType

Rule variants (``ThresholdRule``, ``OptionsRule``, ``JudgedRule``) and the
``Rule`` union are exported for typing and construction.
"""

from __future__ import annotations

from .errors import ConfigError
from .loader import load, loads
from .models import (
    Config,
    Goal,
    GoalType,
    JudgedRule,
    OptionsRule,
    Rule,
    ThresholdRule,
)

__all__ = [
    "Config",
    "ConfigError",
    "Goal",
    "GoalType",
    "JudgedRule",
    "OptionsRule",
    "Rule",
    "ThresholdRule",
    "load",
    "loads",
]
