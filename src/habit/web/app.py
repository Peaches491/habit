"""A small Flask app that renders a daily check-in form from the config.

The form is auto-generated from the parsed goals: one field per goal, with the
widget chosen by the goal's type (and free-text for agent-judged goals). The
config is reloaded on each request, so editing the YAML just needs a refresh.

On submit, the answers are stored raw (source of truth) and scored immediately
so the confirmation page can show line items, the total, and any goals still
awaiting an agent judgment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
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
from ..storage import StorageAdapter, adapter_for_path


# Option goals with this many choices or fewer render as a joined row of
# toggle buttons instead of a dropdown; longer lists fall back to <select>.
_SEGMENTED_MAX = 5

# A bare identifier like "local_drink" is a Material Symbols icon name (drawn
# via the ligature webfont); anything else (an emoji, a symbol) renders as-is.
_MATERIAL_ICON_RE = re.compile(r"^[a-z0-9_]+$")


@dataclass(frozen=True)
class FieldSpec:
    """A single rendered form field, derived from a goal."""

    name: str
    label: str
    widget: str  # "toggle" | "number" | "segmented" | "select" | "textarea"
    choices: tuple[str, ...] | None
    shortcuts: tuple[int | float, ...] | None
    hint: str
    icon: str | None
    icon_is_material: bool
    current: AnswerValue | None  # this goal's answer on the day being viewed, if any


@dataclass(frozen=True)
class DaySummary:
    """One sidebar entry: a day plus its total, if it's been logged."""

    date: str
    label: str
    points: int | None  # None -> not logged yet, rendered as "--"
    selected: bool


def _widget_for(goal: Goal) -> tuple[str, tuple[str, ...] | None]:
    # Judged goals take free text the agent will read, whatever the declared type.
    if isinstance(goal.value, JudgedRule):
        return "textarea", None
    if goal.type is GoalType.BOOL:
        return "toggle", None
    if goal.type is GoalType.NUMBER:
        return "number", None
    if goal.type is GoalType.OPTION:
        choices = tuple(goal.choices or ())
        widget = "segmented" if len(choices) <= _SEGMENTED_MAX else "select"
        return widget, choices
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


def _scoring_meta(goal: Goal) -> dict:
    """Describe how a goal's deterministic points are computed, as JSON the
    form page can use for a client-side running-total *preview*.

    The engine (``habit.scoring``) remains the only place that actually
    computes a day's score — this is a duplicate of just the arithmetic, for
    instant feedback before submit. A judged goal can't be previewed (no
    verdict exists yet), so it's marked pending instead of given points.
    """
    value = goal.value
    if isinstance(value, JudgedRule):
        return {"kind": "judged"}
    if isinstance(value, ThresholdRule):
        return {"kind": "threshold", "at_least": value.at_least, "points": value.points}
    if isinstance(value, OptionsRule):
        return {"kind": "choice_points", "points_by_choice": value.points_by_choice}
    if goal.type is GoalType.NUMBER:
        return {"kind": "flat_number", "points": value}
    if goal.type is GoalType.OPTION:
        return {"kind": "flat_option", "points": value}
    return {"kind": "flat_bool", "points": value}


def _page_title(config: Config) -> str:
    if config.title:
        return f"{config.title} - Check-in"
    if config.user:
        return f"Check-in — {config.user}"
    return "Daily check-in"


def _ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _human_date(day: date) -> str:
    """E.g. "Monday, February 6th, 1776" — for the heading at the top of the form."""
    return f"{day.strftime('%A, %B')} {_ordinal(day.day)}, {day.year}"


def build_fields(config: Config, answers: dict[str, AnswerValue] | None = None) -> list[FieldSpec]:
    """Turn a parsed config into the list of form fields to render.

    ``answers`` prefills each field with that day's existing value (from the
    sidebar's "load a past day" flow); omit it for a fresh, empty form.
    """
    fields: list[FieldSpec] = []
    for goal in config.goals:
        widget, choices = _widget_for(goal)
        fields.append(
            FieldSpec(
                name=goal.name,
                label=goal.description,
                widget=widget,
                choices=choices,
                shortcuts=tuple(goal.shortcuts) if goal.shortcuts else None,
                hint=_hint(goal),
                icon=goal.icon,
                icon_is_material=bool(goal.icon and _MATERIAL_ICON_RE.fullmatch(goal.icon)),
                current=(answers or {}).get(goal.name),
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


# How many days (including today) the sidebar shows.
_SIDEBAR_WINDOW_DAYS = 14


def _answers_for(storage: StorageAdapter, day: date) -> dict[str, AnswerValue] | None:
    """The raw answers already logged for ``day``, or ``None`` if unlogged.

    A dedicated lookup (not just filtering the sidebar's window read) so it's
    correct even for a day outside that window — e.g. a stale bookmarked link.
    """
    return next((d.answers for d in storage.read_raw(day) if d.date == day), None)


def _sidebar_days(
    config: Config,
    storage: StorageAdapter,
    today: date,
    selected: date,
    window_days: int = _SIDEBAR_WINDOW_DAYS,
) -> list[DaySummary]:
    """The last ``window_days`` days (today first), each with its total if logged."""
    window_start = today - timedelta(days=window_days - 1)
    raw_by_date = {d.date: d.answers for d in storage.read_raw(window_start)}
    days = []
    for offset in range(window_days):
        day = today - timedelta(days=offset)
        answers = raw_by_date.get(day)
        points = score_day(config, DayLog(answers=answers)).total if answers is not None else None
        days.append(
            DaySummary(
                date=day.isoformat(),
                label=day.strftime("%a, %b %d"),
                points=points,
                selected=(day == selected),
            )
        )
    return days


def _parse_answers(config: Config, form) -> dict[str, AnswerValue]:  # type: ignore[no-untyped-def]
    """Turn submitted form data into a raw answers dict, one key per logged goal.

    A field the user left blank (or a toggle/segmented group left untouched)
    is simply omitted (-> SKIPPED by the engine). Numbers that don't parse are
    passed through as the raw string so the engine flags them INVALID rather
    than silently dropping bad input.
    """
    answers: dict[str, AnswerValue] = {}
    for goal in config.goals:
        widget, _ = _widget_for(goal)
        raw = form.get(goal.name, "").strip()
        if not raw:
            continue
        if widget == "toggle":
            answers[goal.name] = raw == "true"
        elif widget == "number":
            try:
                num = float(raw)
                answers[goal.name] = int(num) if num.is_integer() else num
            except ValueError:
                answers[goal.name] = raw
        else:  # segmented, select, or textarea
            answers[goal.name] = raw
    return answers


def create_app(config_path: str, storage: StorageAdapter | None = None) -> Flask:
    """Build the Flask app serving the check-in form for ``config_path``."""
    app = Flask(__name__)
    app.config["HABIT_CONFIG_PATH"] = str(config_path)
    if storage is None:
        storage = adapter_for_path(Path(config_path).with_name("habit_data.json"))

    @app.get("/")
    def index():  # type: ignore[no-untyped-def]
        try:
            config = load(app.config["HABIT_CONFIG_PATH"])
        except ConfigError as exc:
            return render_template_string(ERROR_TEMPLATE, error=str(exc)), 400
        today = date.today()
        selected = _parse_date(request.args.get("date"))
        selected_answers = _answers_for(storage, selected)
        locked = config.lock_submitted_days and selected_answers is not None
        return render_template_string(
            FORM_TEMPLATE,
            title=_page_title(config),
            fields=build_fields(config, selected_answers),
            scoring_meta={goal.name: _scoring_meta(goal) for goal in config.goals},
            today=today.isoformat(),
            yesterday=(today - timedelta(days=1)).isoformat(),
            selected_date=selected.isoformat(),
            human_date=_human_date(selected),
            is_today=(selected == today),
            locked=locked,
            sidebar_days=_sidebar_days(config, storage, today, selected),
        )

    @app.post("/checkin")
    def checkin():  # type: ignore[no-untyped-def]
        try:
            config = load(app.config["HABIT_CONFIG_PATH"])
        except ConfigError as exc:
            return render_template_string(ERROR_TEMPLATE, error=str(exc)), 400

        day = _parse_date(request.form.get("_date"))

        if config.lock_submitted_days and _answers_for(storage, day) is not None:
            error = (
                f"{day.isoformat()} is already logged and locked for editing "
                "(lock_submitted_days is enabled in the config)."
            )
            return render_template_string(ERROR_TEMPLATE, error=error), 403

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
  <link rel="stylesheet"
        href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined">
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
    /* Left padding reserves room for the sidebar, which is anchored to the
       viewport edge (position: fixed) rather than laid out next to the card,
       so it stays put on the side of the page as the card scrolls/centers. */
    body { min-height: 100vh; padding: 3.5rem 1.25rem 3.5rem 16rem; background: var(--nord0); }
    .habit-shell { max-width: 42rem; margin: 0 auto; }
    .habit-sidebar {
      position: fixed; top: 3.5rem; left: 1.5rem;
      width: 12.5rem; max-height: calc(100vh - 5rem); overflow-y: auto;
      display: flex; flex-direction: column; gap: .55rem;
    }
    .habit-day {
      display: flex; flex-direction: column; gap: .1rem; text-decoration: none;
      background: var(--nord1); border: 1px solid var(--nord3); border-radius: .6rem;
      padding: .5rem .75rem;
    }
    .habit-day:hover { border-color: var(--nord8); }
    .habit-day.selected { border-color: var(--nord8); background: var(--nord2); }
    .habit-day-date { font-size: .78rem; color: rgba(236, 239, 244, .65); }
    .habit-day-points { font-size: 1.05rem; font-weight: 600; color: var(--nord6); }
    @media (max-width: 900px) {
      body { padding-left: 1.25rem; }
      .habit-sidebar {
        position: static; width: 100%; max-height: none;
        flex-direction: row; overflow-x: auto; margin-bottom: 1.25rem;
      }
      .habit-day { flex: 0 0 auto; min-width: 7rem; }
    }
    .habit-card {
      background: var(--nord1);
      border: 1px solid var(--nord3);
      border-radius: 1rem;
      padding: 2.5rem;
    }
    .habit-title { font-weight: 700; color: var(--nord6); margin-bottom: .35rem; }
    .habit-date-heading { color: var(--nord4); font-size: 1rem; margin: 0 0 .5rem; }
    .habit-total { font-size: 1.35rem; font-weight: 700; color: var(--nord6); }
    .habit-total .accent { color: var(--nord8); }
    .habit-link { color: var(--nord8); text-decoration: underline; font-size: .9rem; }
    .habit-link:hover { color: var(--nord9); }
    .form-text { font-size: .8rem; color: rgba(236, 239, 244, .65); }
    /* Goal prompts (not the date picker) get extra size/weight to stand out. */
    .habit-prompt { display: block; font-size: 1.08rem; font-weight: 600; color: var(--nord6); margin-bottom: .6rem; }
    .material-symbols-outlined {
      font-family: 'Material Symbols Outlined';
      font-weight: normal; font-style: normal; font-size: 1.15em; line-height: 1;
      vertical-align: -.15em; white-space: nowrap; word-wrap: normal; direction: ltr;
    }

    /* Each field sits in its own inset panel so goals read as clearly separate.
       The icon (if any) is a flush left column, full-height and centered; the
       body carries the field's own padding. */
    .habit-field {
      display: flex;
      background: var(--nord0);
      border: 1px solid var(--nord3);
      border-radius: .75rem;
      overflow: hidden;
    }
    .habit-field + .habit-field { margin-top: 1rem; }
    .habit-field-icon {
      display: flex; align-items: center; justify-content: center;
      flex: 0 0 3rem; font-size: 1.3em; color: var(--nord8);
      border-right: 1px dashed var(--nord3);
    }
    .habit-field-body { flex: 1 1 auto; min-width: 0; padding: 1.1rem 1.2rem 1.1rem .9rem; }

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
    /* Yes/No + segmented option buttons: unchecked outline, checked -> frost blue fill. */
    .btn-outline-primary {
      --bs-btn-color: var(--nord4); --bs-btn-border-color: var(--nord3);
      --bs-btn-hover-color: var(--nord0); --bs-btn-hover-bg: var(--nord8); --bs-btn-hover-border-color: var(--nord8);
      --bs-btn-active-color: var(--nord0); --bs-btn-active-bg: var(--nord8); --bs-btn-active-border-color: var(--nord8);
      --bs-btn-focus-shadow-rgb: 136, 192, 208;
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
    .habit-alert-info { background: rgba(136, 192, 208, .12); border: 1px solid var(--nord8); }
    .habit-alert-info strong { color: var(--nord8); }
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
  {% macro prompt_icon(f) %}{% if f.icon_is_material %}<span class="material-symbols-outlined" aria-hidden="true">{{ f.icon }}</span>{% else %}<span aria-hidden="true">{{ f.icon }}</span>{% endif %}{% endmacro %}
  <div class="habit-shell">
    <nav class="habit-sidebar" aria-label="Recent days">
      {% for d in sidebar_days %}
      <a href="/?date={{ d.date }}" class="habit-day{% if d.selected %} selected{% endif %}">
        <span class="habit-day-date">{{ d.label }}</span>
        <span class="habit-day-points">{% if d.points is not none %}{{ d.points }} pts{% else %}--{% endif %}</span>
      </a>
      {% endfor %}
    </nav>
    <div class="habit-card">
      <h1 class="habit-title h3">{{ title }}</h1>
      <p class="habit-date-heading">{{ human_date }}</p>
      <a href="#" class="habit-link d-block mb-3" id="date-reveal">Logging a different day?</a>
      <div class="habit-total mb-3">{% if is_today %}Today's total{% else %}Total for {{ selected_date }}{% endif %}: <span class="accent" id="habit-running-total">0 pts</span></div>
      {% if locked %}
      <div class="habit-alert habit-alert-info mb-3">
        <strong>{{ selected_date }} is already logged.</strong> Editing is
        locked (<code>lock_submitted_days</code> is enabled in the config).
      </div>
      {% endif %}
      {% set dis = "disabled" if locked else "" %}
      <form method="post" action="/checkin">
        <div id="date-field" class="d-none mb-3">
          <label class="form-label" for="_date">Date</label>
          <input type="date" class="form-control" id="_date" name="_date" value="{{ selected_date }}" {{ dis }}>
          <div class="btn-group w-100 mt-2" role="group" aria-label="Quick date select">
            <button type="button" class="btn btn-outline-primary flex-fill shortcut-btn" id="date-today"
                    data-target="_date" data-value="{{ today }}" {{ dis }}>Today</button>
            <button type="button" class="btn btn-outline-primary flex-fill shortcut-btn" id="date-yesterday"
                    data-target="_date" data-value="{{ yesterday }}" {{ dis }}>Yesterday</button>
          </div>
        </div>
        {% for f in fields %}
        <div class="habit-field">
          {% if f.icon %}<div class="habit-field-icon">{{ prompt_icon(f) }}</div>{% endif %}
          <div class="habit-field-body">
          {% if f.widget == "toggle" %}
            <div class="habit-prompt" id="{{ f.name }}-label">{{ f.label }}</div>
            <div class="btn-group w-100" role="group" aria-labelledby="{{ f.name }}-label">
              <input type="radio" class="btn-check" name="{{ f.name }}" id="{{ f.name }}-yes"
                     value="true" autocomplete="off" {% if f.current == true %}checked{% endif %} {{ dis }}>
              <label class="btn btn-outline-primary flex-fill" for="{{ f.name }}-yes">Yes</label>
              <input type="radio" class="btn-check" name="{{ f.name }}" id="{{ f.name }}-no"
                     value="false" autocomplete="off" {% if f.current == false %}checked{% endif %} {{ dis }}>
              <label class="btn btn-outline-primary flex-fill" for="{{ f.name }}-no">No</label>
            </div>
          {% elif f.widget == "segmented" %}
            <div class="habit-prompt" id="{{ f.name }}-label">{{ f.label }}</div>
            <div class="btn-group w-100" role="group" aria-labelledby="{{ f.name }}-label">
              {% for c in f.choices %}
              <input type="radio" class="btn-check" name="{{ f.name }}" id="{{ f.name }}-{{ loop.index }}"
                     value="{{ c }}" autocomplete="off" {% if f.current == c %}checked{% endif %} {{ dis }}>
              <label class="btn btn-outline-primary flex-fill" for="{{ f.name }}-{{ loop.index }}">{{ c }}</label>
              {% endfor %}
            </div>
          {% else %}
            <label class="habit-prompt" for="{{ f.name }}">{{ f.label }}</label>
            {% if f.widget == "number" %}
              <input type="number" step="any" class="form-control" id="{{ f.name }}"
                     name="{{ f.name }}" value="{{ f.current if f.current is not none else '' }}" {{ dis }}>
              {% if f.shortcuts %}
              <div class="btn-group w-100 mt-2" role="group" aria-label="Quick values for {{ f.label }}">
                {% for s in f.shortcuts %}
                <button type="button" class="btn btn-outline-primary flex-fill shortcut-btn"
                        data-target="{{ f.name }}" data-value="{{ s }}" {{ dis }}>{{ s }}</button>
                {% endfor %}
              </div>
              {% endif %}
            {% elif f.widget == "select" %}
              <select class="form-select" id="{{ f.name }}" name="{{ f.name }}" {{ dis }}>
                <option value="">—</option>
                {% for c in f.choices %}
                <option value="{{ c }}" {% if f.current == c %}selected{% endif %}>{{ c }}</option>
                {% endfor %}
              </select>
            {% elif f.widget == "textarea" %}
              <textarea class="form-control" id="{{ f.name }}" name="{{ f.name }}"
                        rows="3" {{ dis }}>{{ f.current if f.current is not none else '' }}</textarea>
            {% endif %}
          {% endif %}
          <div class="form-text">{{ f.hint }}</div>
          </div>
        </div>
        {% endfor %}
        {% if not locked %}
        <button type="submit" class="btn btn-primary w-100 mt-3">Submit check-in</button>
        {% endif %}
      </form>
    </div>
  </div>
  <script id="habit-scoring-meta" type="application/json">{{ scoring_meta | tojson }}</script>
  <script>
    (function () {
      var groups = {};
      document.querySelectorAll('.shortcut-btn').forEach(function (btn) {
        (groups[btn.dataset.target] = groups[btn.dataset.target] || []).push(btn);
      });

      Object.keys(groups).forEach(function (name) {
        var input = document.getElementById(name);
        var buttons = groups[name];
        if (!input) return;

        function sync() {
          buttons.forEach(function (btn) {
            var match = input.value === btn.dataset.value;
            btn.classList.toggle('active', match);
            btn.setAttribute('aria-pressed', match);
          });
        }

        buttons.forEach(function (btn) {
          btn.addEventListener('click', function () {
            input.value = btn.dataset.value;
            input.dispatchEvent(new Event('input', { bubbles: true }));
            sync();
          });
        });
        input.addEventListener('input', sync);
        sync();
      });
    })();

    (function () {
      var revealLink = document.getElementById('date-reveal');
      var dateField = document.getElementById('date-field');
      if (!revealLink || !dateField) return;
      revealLink.addEventListener('click', function (e) {
        e.preventDefault();
        dateField.classList.remove('d-none');
        revealLink.classList.add('d-none');
      });
    })();

    (function () {
      // Live preview only: mirrors the *deterministic* half of the scoring
      // engine for instant feedback. Judged goals can't be previewed (no
      // verdict yet) and count as pending instead. The authoritative total
      // is always recomputed server-side by habit.scoring on submit.
      var scoringMeta = JSON.parse(document.getElementById('habit-scoring-meta').textContent);
      var totalEl = document.getElementById('habit-running-total');

      function currentAnswer(name) {
        var radios = document.querySelectorAll('input[type="radio"][name="' + name + '"]');
        if (radios.length) {
          var checked = document.querySelector('input[type="radio"][name="' + name + '"]:checked');
          return checked ? checked.value : null;
        }
        var el = document.getElementById(name);
        return el ? el.value : null;
      }

      function computeTotal() {
        var total = 0;
        var pending = 0;
        Object.keys(scoringMeta).forEach(function (name) {
          var meta = scoringMeta[name];
          var raw = currentAnswer(name);
          if (raw === null || raw === '') return;
          if (meta.kind === 'judged') { pending += 1; return; }
          if (meta.kind === 'flat_bool') { if (raw === 'true') total += meta.points; return; }
          if (meta.kind === 'flat_number') { var n = parseFloat(raw); if (!isNaN(n) && n > 0) total += meta.points; return; }
          if (meta.kind === 'threshold') { var m = parseFloat(raw); if (!isNaN(m) && m >= meta.at_least) total += meta.points; return; }
          if (meta.kind === 'flat_option') { total += meta.points; return; }
          if (meta.kind === 'choice_points') { total += (meta.points_by_choice[raw] || 0); return; }
        });
        totalEl.textContent = total + ' pts' + (pending ? ' (+' + pending + ' pending)' : '');
      }

      Object.keys(scoringMeta).forEach(function (name) {
        var radios = document.querySelectorAll('input[type="radio"][name="' + name + '"]');
        if (radios.length) {
          radios.forEach(function (r) { r.addEventListener('change', computeTotal); });
          return;
        }
        var el = document.getElementById(name);
        if (el) {
          el.addEventListener('input', computeTotal);
          el.addEventListener('change', computeTotal);
        }
      });
      computeTotal();
    })();
  </script>
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
