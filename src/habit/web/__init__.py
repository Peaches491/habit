"""Habit web UI: a Flask app serving the daily check-in form.

    from habit.web import create_app
    app = create_app("habit.yaml")

Or run it directly: ``python -m habit.web --config habit.yaml`` (or the
``habit-web`` console script).
"""

from __future__ import annotations

from .app import FieldSpec, build_fields, create_app

__all__ = ["FieldSpec", "build_fields", "create_app"]
