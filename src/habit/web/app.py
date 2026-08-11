"""A small Flask app that renders a daily check-in form from the config.

The form is auto-generated from the parsed goals: one field per goal, with the
widget chosen by the goal's type (and free-text for agent-judged goals). The
config is reloaded on each request, so editing the YAML just needs a refresh.

The POST handler is a stub for now — wiring it to scoring/storage comes next.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

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


def create_app(config_path: str) -> Flask:
    """Build the Flask app serving the check-in form for ``config_path``."""
    app = Flask(__name__)
    app.config["HABIT_CONFIG_PATH"] = str(config_path)

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
        # STUB: next step parses these into a DayLog, scores, and stores them.
        # Unchecked checkboxes are simply absent from request.form.
        received = {key: value for key, value in request.form.items()}
        return render_template_string(RECEIVED_TEMPLATE, received=received)

    return app


_STYLE = """
  :root { color-scheme: light dark; }
  body { font: 16px/1.5 system-ui, sans-serif; max-width: 40rem; margin: 2rem auto;
         padding: 0 1rem; }
  h1 { font-size: 1.4rem; }
  .field { margin: 1.1rem 0; }
  .label { display: block; font-weight: 600; margin-bottom: .25rem; }
  .checkbox { display: flex; align-items: center; gap: .5rem; font-weight: 600; }
  .checkbox input { width: 1.1rem; height: 1.1rem; }
  input[type=number], select, textarea, input[type=date] {
    width: 100%; padding: .5rem; font: inherit; box-sizing: border-box; }
  .hint { display: block; font-size: .85rem; opacity: .7; margin-top: .25rem; }
  button { margin-top: 1rem; padding: .6rem 1.2rem; font: inherit; font-weight: 600;
           cursor: pointer; }
  .stub { background: #ffd; color: #663; padding: .5rem .75rem; border-radius: .3rem;
          font-size: .9rem; }
  @media (prefers-color-scheme: dark) { .stub { background: #443; color: #ffd; } }
"""

FORM_TEMPLATE = (
    """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ title }}</title>
  <style>"""
    + _STYLE
    + """</style>
</head>
<body>
  <h1>{{ title }}</h1>
  <form method="post" action="/checkin">
    <div class="field">
      <label class="label" for="_date">Date</label>
      <input type="date" id="_date" name="_date" value="{{ today }}">
    </div>
    {% for f in fields %}
    <div class="field">
      {% if f.widget == "checkbox" %}
        <label class="checkbox">
          <input type="checkbox" name="{{ f.name }}" value="true">
          <span>{{ f.label }}</span>
        </label>
      {% else %}
        <label class="label" for="{{ f.name }}">{{ f.label }}</label>
        {% if f.widget == "number" %}
          <input type="number" step="any" id="{{ f.name }}" name="{{ f.name }}">
        {% elif f.widget == "select" %}
          <select id="{{ f.name }}" name="{{ f.name }}">
            <option value="">—</option>
            {% for c in f.choices %}<option value="{{ c }}">{{ c }}</option>{% endfor %}
          </select>
        {% elif f.widget == "textarea" %}
          <textarea id="{{ f.name }}" name="{{ f.name }}" rows="3"></textarea>
        {% endif %}
      {% endif %}
      <span class="hint">{{ f.hint }}</span>
    </div>
    {% endfor %}
    <button type="submit">Submit check-in</button>
  </form>
</body>
</html>
"""
)

RECEIVED_TEMPLATE = (
    """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Received</title>
  <style>"""
    + _STYLE
    + """</style>
</head>
<body>
  <h1>Check-in received</h1>
  <p class="stub">Stub handler — not yet scored or stored.</p>
  <ul>
    {% for key, value in received.items() %}
    <li><strong>{{ key }}</strong>: {{ value }}</li>
    {% endfor %}
  </ul>
  <p><a href="/">Back to the form</a></p>
</body>
</html>
"""
)

ERROR_TEMPLATE = (
    """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Config error</title>
  <style>"""
    + _STYLE
    + """</style>
</head>
<body>
  <h1>Config error</h1>
  <pre>{{ error }}</pre>
</body>
</html>
"""
)
