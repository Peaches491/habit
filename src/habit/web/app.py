"""A small Flask app that renders a daily check-in form from the config.

The form is auto-generated from the parsed goals: one field per goal, with the
widget chosen by the goal's type (and free-text for agent-judged goals). The
config is reloaded on each request, so editing the YAML just needs a refresh.

On submit, the answers are stored raw (source of truth) and scored immediately
so the confirmation page can show line items, the total, and any goals still
awaiting an agent judgment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from flask import Flask, render_template_string, request

from ..config import (
    ConfigError,
    Config,
    GoalType,
    JudgedRule,
    OptionsRule,
    ThresholdRule,
    load,
)
from ..config.models import Goal
from ..scoring import AnswerValue, DayLog, score_day
from ..storage import JsonFileStorageAdapter, StorageAdapter


@dataclass(frozen=True)
class FieldSpec:
    """A single rendered form field, derived from a goal."""

    name: str
    label: str
    widget: str  # "checkbox" | "number" | "select" | "textarea"
    choices: tuple[str, ...] | None
    hint: str


def _widget_for(goal: Goal) -> tuple[str, tuple[str, ...] | None]:
    # Judged goals take free text the agent will read, whatever the declared type.
    if isinstance(goal.value, JudgedRule):
        return "textarea", None
    if goal.type is GoalType.BOOL:
        return "checkbox", None
    if goal.type is GoalType.NUMBER:
        return "number", None
    if goal.type is GoalType.OPTION:
        return "select", tuple(goal.choices or ())
    raise ValueError(f"no widget for goal type {goal.type}")  # defensive


def _hint(goal: Goal) -> str:
    value = goal.value
    if isinstance(value, ThresholdRule):
        return f"{value.points} pts if ≥ {value.at_least}"
    if isinstance(value, OptionsRule):
        return "points vary by choice"
    if isinstance(value, JudgedRule):
        return f"up to {value.points} pts — reviewed by the agent"
    return f"{value} pts"


def build_fields(config: Config) -> list[FieldSpec]:
    """Turn a parsed config into the list of form fields to render."""
    fields: list[FieldSpec] = []
    for goal in config.goals:
        widget, choices = _widget_for(goal)
        fields.append(
            FieldSpec(
                name=goal.name,
                label=goal.description,
                widget=widget,
                choices=choices,
                hint=_hint(goal),
            )
        )
    return fields


def _parse_date(raw: str | None) -> date:
    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    return date.today()


def _parse_answers(config: Config, form) -> dict[str, AnswerValue]:  # type: ignore[no-untyped-def]
    """Turn submitted form data into a raw answers dict, one key per logged goal.

    A field the user left blank is simply omitted (-> SKIPPED by the engine).
    Numbers that don't parse are passed through as the raw string so the engine
    flags them INVALID rather than silently dropping bad input.
    """
    answers: dict[str, AnswerValue] = {}
    for goal in config.goals:
        widget, _ = _widget_for(goal)
        if widget == "checkbox":
            answers[goal.name] = goal.name in form
            continue
        raw = form.get(goal.name, "").strip()
        if not raw:
            continue
        if widget == "number":
            try:
                num = float(raw)
                answers[goal.name] = int(num) if num.is_integer() else num
            except ValueError:
                answers[goal.name] = raw
        else:  # select or textarea
            answers[goal.name] = raw
    return answers


def create_app(config_path: str, storage: StorageAdapter | None = None) -> Flask:
    """Build the Flask app serving the check-in form for ``config_path``."""
    app = Flask(__name__)
    app.config["HABIT_CONFIG_PATH"] = str(config_path)
    if storage is None:
        storage = JsonFileStorageAdapter(Path(config_path).with_name("habit_data.json"))

    @app.get("/")
    def index():  # type: ignore[no-untyped-def]
        try:
            config = load(app.config["HABIT_CONFIG_PATH"])
        except ConfigError as exc:
            return render_template_string(ERROR_TEMPLATE, error=str(exc)), 400
        title = f"Check-in — {config.user}" if config.user else "Daily check-in"
        return render_template_string(
            FORM_TEMPLATE,
            title=title,
            fields=build_fields(config),
            today=date.today().isoformat(),
        )

    @app.post("/checkin")
    def checkin():  # type: ignore[no-untyped-def]
        try:
            config = load(app.config["HABIT_CONFIG_PATH"])
        except ConfigError as exc:
            return render_template_string(ERROR_TEMPLATE, error=str(exc)), 400

        day = _parse_date(request.form.get("_date"))
        answers = _parse_answers(config, request.form)
        storage.upsert_raw(day, answers)
        score = score_day(config, DayLog(answers=answers))
        storage.write_scores(day, score)

        return render_template_string(
            RESULT_TEMPLATE,
            day=day.isoformat(),
            items=score.items,
            total=score.total,
            pending=score.pending,
            status_badge=_STATUS_BADGE,
        )

    return app


# Bootstrap supplies form/table/badge/button structure; this layer is a
# Nord-inspired dark reskin (navy bg, frost-blue accent) on top of it. Bootstrap
# bakes its own variant colors as literal hex into component-scoped custom
# properties (e.g. .btn-primary's --bs-btn-bg), so re-theming means overriding
# those per-component vars rather than the global --bs-primary. The "text-bg-*"
# badge utilities go further and mark color/background !important, so status
# badges use dedicated .badge-nord-* classes instead of fighting that.
_HEAD = """
  <link rel="stylesheet"
        href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
  <meta name="color-scheme" content="dark">
  <style>
    :root {
      --nord0: #2e3440; --nord1: #3b4252; --nord2: #434c5e; --nord3: #4c566a;
      --nord4: #d8dee9; --nord6: #eceff4; --nord8: #88c0d0; --nord9: #81a1c1;
      --nord10: #5e81ac; --nord11: #bf616a; --nord13: #ebcb8b; --nord14: #a3be8c;
      --bs-body-bg: var(--nord0);
      --bs-body-color: var(--nord6);
      --bs-border-color: var(--nord3);
    }
    body { min-height: 100vh; padding: 3.5rem 1.25rem; background: var(--nord0); }
    .habit-shell { max-width: 42rem; margin: 0 auto; }
    .habit-card {
      background: var(--nord1);
      border: 1px solid var(--nord3);
      border-radius: 1rem;
      padding: 2.5rem;
    }
    .habit-title { font-weight: 700; color: var(--nord6); margin-bottom: 1.75rem; }
    .habit-total { font-size: 1.35rem; font-weight: 700; color: var(--nord6); }
    .habit-total .accent { color: var(--nord8); }
    .form-text { font-size: .8rem; color: rgba(236, 239, 244, .65); }
    .form-check-label { color: var(--nord6); }

    .form-control, .form-select {
      background-color: var(--nord2); border-color: var(--nord3); color: var(--nord6);
    }
    .form-control:focus, .form-select:focus {
      background-color: var(--nord2); border-color: var(--nord8); color: var(--nord6);
      box-shadow: 0 0 0 .25rem rgba(136, 192, 208, .25);
    }
    .form-select {
      background-image: url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3e%3cpath fill='none' stroke='%23d8dee9' stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M2 5l6 6 6-6'/%3e%3c/svg%3e");
    }
    .form-check-input { background-color: var(--nord2); border-color: var(--nord3); }
    .form-check-input:checked { background-color: var(--nord8); border-color: var(--nord8); }
    .form-check-input:focus {
      border-color: var(--nord8); box-shadow: 0 0 0 .25rem rgba(136, 192, 208, .25);
    }

    .btn-primary {
      --bs-btn-color: var(--nord0); --bs-btn-bg: var(--nord8); --bs-btn-border-color: var(--nord8);
      --bs-btn-hover-color: var(--nord0); --bs-btn-hover-bg: #9fccd8; --bs-btn-hover-border-color: #9fccd8;
      --bs-btn-active-color: var(--nord0); --bs-btn-active-bg: var(--nord9); --bs-btn-active-border-color: var(--nord9);
      --bs-btn-focus-shadow-rgb: 136, 192, 208;
      --bs-btn-disabled-color: var(--nord0); --bs-btn-disabled-bg: var(--nord8); --bs-btn-disabled-border-color: var(--nord8);
    }
    .btn-outline-secondary {
      --bs-btn-color: var(--nord4); --bs-btn-border-color: var(--nord3);
      --bs-btn-hover-color: var(--nord6); --bs-btn-hover-bg: var(--nord2); --bs-btn-hover-border-color: var(--nord3);
      --bs-btn-active-color: var(--nord6); --bs-btn-active-bg: var(--nord2); --bs-btn-active-border-color: var(--nord3);
    }

    .table { --bs-table-color: var(--nord6); --bs-table-border-color: var(--nord3); }
    .table-hover > tbody > tr:hover > * { background-color: rgba(136, 192, 208, .08); color: var(--nord6); }
    .text-muted { color: rgba(236, 239, 244, .6) !important; }

    .badge-nord-success { background: var(--nord14); color: var(--nord0); }
    .badge-nord-secondary { background: var(--nord3); color: var(--nord6); }
    .badge-nord-warning { background: var(--nord13); color: var(--nord0); }
    .badge-nord-danger { background: var(--nord11); color: var(--nord0); }

    .habit-alert {
      border-radius: .5rem; padding: 1rem; color: var(--nord6);
    }
    .habit-alert-warn { background: rgba(235, 203, 139, .12); border: 1px solid var(--nord13); }
    .habit-alert-warn strong { color: var(--nord13); }
    .habit-alert-danger { background: rgba(191, 97, 106, .12); border: 1px solid var(--nord11); }
  </style>
"""

# LineItem.status.value -> .badge-nord-* suffix (see _HEAD).
_STATUS_BADGE = {
    "scored": "success",
    "skipped": "secondary",
    "pending_judgment": "warning",
    "invalid": "danger",
}

FORM_TEMPLATE = (
    """
<!doctype html>
<html lang="en" data-bs-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ title }}</title>"""
    + _HEAD
    + """
</head>
<body>
  <div class="habit-shell">
    <div class="habit-card">
      <h1 class="habit-title h3">{{ title }}</h1>
      <form method="post" action="/checkin">
        <div class="mb-3">
          <label class="form-label" for="_date">Date</label>
          <input type="date" class="form-control" id="_date" name="_date" value="{{ today }}">
        </div>
        {% for f in fields %}
        {% if f.widget == "checkbox" %}
          <div class="mb-3 form-check">
            <input type="checkbox" class="form-check-input" id="{{ f.name }}"
                   name="{{ f.name }}" value="true">
            <label class="form-check-label" for="{{ f.name }}">{{ f.label }}</label>
            <div class="form-text">{{ f.hint }}</div>
          </div>
        {% else %}
          <div class="mb-3">
            <label class="form-label" for="{{ f.name }}">{{ f.label }}</label>
            {% if f.widget == "number" %}
              <input type="number" step="any" class="form-control" id="{{ f.name }}"
                     name="{{ f.name }}">
            {% elif f.widget == "select" %}
              <select class="form-select" id="{{ f.name }}" name="{{ f.name }}">
                <option value="">—</option>
                {% for c in f.choices %}<option value="{{ c }}">{{ c }}</option>{% endfor %}
              </select>
            {% elif f.widget == "textarea" %}
              <textarea class="form-control" id="{{ f.name }}" name="{{ f.name }}"
                        rows="3"></textarea>
            {% endif %}
            <div class="form-text">{{ f.hint }}</div>
          </div>
        {% endif %}
        {% endfor %}
        <button type="submit" class="btn btn-primary w-100 mt-2">Submit check-in</button>
      </form>
    </div>
  </div>
</body>
</html>
"""
)

RESULT_TEMPLATE = (
    """
<!doctype html>
<html lang="en" data-bs-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Check-in received</title>"""
    + _HEAD
    + """
</head>
<body>
  <div class="habit-shell">
    <div class="habit-card">
      <h1 class="habit-title h3">Check-in received — {{ day }}</h1>
      <table class="table table-hover align-middle">
        <thead>
          <tr><th>Goal</th><th>Status</th><th>Detail</th><th class="text-end">Points</th></tr>
        </thead>
        <tbody>
          {% for item in items %}
          <tr>
            <td>{{ item.goal }}</td>
            <td><span class="badge badge-nord-{{ status_badge[item.status.value] }}">{{ item.status.value }}</span></td>
            <td class="text-muted">{{ item.detail }}</td>
            <td class="text-end">{{ item.points }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      <p class="habit-total">Total: <span class="accent">{{ total }} pts</span></p>
      {% if pending %}
      <div class="habit-alert habit-alert-warn mt-4">
        <strong>Awaiting judgment</strong>
        <ul class="mb-0">
          {% for req in pending %}
          <li>{{ req.goal }} (up to {{ req.max_points }} pts) — {{ req.prompt }}</li>
          {% endfor %}
        </ul>
      </div>
      {% endif %}
      <a href="/" class="btn btn-outline-secondary mt-3">Back to the form</a>
    </div>
  </div>
</body>
</html>
"""
)

ERROR_TEMPLATE = (
    """
<!doctype html>
<html lang="en" data-bs-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Config error</title>"""
    + _HEAD
    + """
</head>
<body>
  <div class="habit-shell">
    <div class="habit-card">
      <h1 class="habit-title h3">Config error</h1>
      <div class="habit-alert habit-alert-danger"><pre class="mb-0">{{ error }}</pre></div>
    </div>
  </div>
</body>
</html>
"""
)
