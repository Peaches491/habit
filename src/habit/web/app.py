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
    is_today: bool
    is_future: bool


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


def _answers_for(storage: StorageAdapter, day: date) -> dict[str, AnswerValue] | None:
    """The raw answers already logged for ``day``, or ``None`` if unlogged."""
    return next((d.answers for d in storage.read_raw(day) if d.date == day), None)


def _week_start(day: date, week_start: str) -> date:
    """The first day of ``day``'s week, per the configured ``week_start``."""
    if week_start == "sunday":
        offset = (day.weekday() + 1) % 7  # Python: Monday=0 ... Sunday=6 -> Sunday=0
    else:
        offset = day.weekday()  # already Monday=0 ... Sunday=6
    return day - timedelta(days=offset)


def _resolve_week_start(config: Config, today: date, requested: date) -> date:
    """The sidebar's week-start date: ``requested``'s week, clamped to never
    be later than the current week (the sidebar never shows a future week)."""
    current = _week_start(today, config.week_start)
    return min(_week_start(requested, config.week_start), current)


@dataclass(frozen=True)
class WeekNav:
    """The sidebar's week-navigation state: label + prev/next targets."""

    label: str
    prev: str
    next: str
    has_next: bool


def _week_nav(config: Config, today: date, week_start_date: date) -> WeekNav:
    week_end = week_start_date + timedelta(days=6)
    return WeekNav(
        label=f"{week_start_date.strftime('%b %d')} – {week_end.strftime('%b %d')}",
        prev=(week_start_date - timedelta(days=7)).isoformat(),
        next=(week_start_date + timedelta(days=7)).isoformat(),
        has_next=week_start_date < _week_start(today, config.week_start),
    )


def _sidebar_days(
    config: Config,
    storage: StorageAdapter,
    today: date,
    selected: date,
    week_start_date: date,
) -> list[DaySummary]:
    """The 7 days of ``week_start_date``'s week, each with its total if logged."""
    raw_by_date = {d.date: d.answers for d in storage.read_raw(week_start_date)}
    days = []
    for offset in range(7):
        day = week_start_date + timedelta(days=offset)
        answers = raw_by_date.get(day)
        points = score_day(config, DayLog(answers=answers)).total if answers is not None else None
        days.append(
            DaySummary(
                date=day.isoformat(),
                label=day.strftime("%a, %b %d"),
                points=points,
                selected=(day == selected),
                is_today=(day == today),
                is_future=(day > today),
            )
        )
    return days


def _overall_total(config: Config, storage: StorageAdapter) -> int:
    """All-time points across every day ever logged."""
    return sum(
        score_day(config, DayLog(answers=d.answers)).total
        for d in storage.read_raw(date.min)
    )


def _sidebar_context(
    config: Config,
    storage: StorageAdapter,
    today: date,
    selected: date,
    week_start_date: date,
) -> dict:
    """Everything the shared sidebar partial needs, for one render call."""
    days = _sidebar_days(config, storage, today, selected, week_start_date)
    return {
        "sidebar_days": days,
        "week_nav": _week_nav(config, today, week_start_date),
        "week_start_date": week_start_date.isoformat(),
        "weekly_total": sum(d.points for d in days if d.points is not None),
        "overall_total": _overall_total(config, storage),
    }


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
            return render_template_string(
                ERROR_TEMPLATE, error=str(exc), sidebar_days=[], week_nav=None, overall_total=None
            ), 400
        today = date.today()
        selected = _parse_date(request.args.get("date"))
        selected_answers = _answers_for(storage, selected)
        locked = config.lock_submitted_days and selected_answers is not None
        week_start_date = _resolve_week_start(config, today, _parse_date(request.args.get("week")))
        return render_template_string(
            FORM_TEMPLATE,
            title=_page_title(config),
            fields=build_fields(config, selected_answers),
            scoring_meta={goal.name: _scoring_meta(goal) for goal in config.goals},
            today=today.isoformat(),
            yesterday=(today - timedelta(days=1)).isoformat(),
            selected_date=selected.isoformat(),
            human_date=_human_date(selected),
            locked=locked,
            **_sidebar_context(config, storage, today, selected, week_start_date),
        )

    @app.post("/checkin")
    def checkin():  # type: ignore[no-untyped-def]
        try:
            config = load(app.config["HABIT_CONFIG_PATH"])
        except ConfigError as exc:
            return render_template_string(
                ERROR_TEMPLATE, error=str(exc), sidebar_days=[], week_nav=None, overall_total=None
            ), 400

        day = _parse_date(request.form.get("_date"))
        today = date.today()
        week_start_date = _resolve_week_start(config, today, day)

        if config.lock_submitted_days and _answers_for(storage, day) is not None:
            error = (
                f"{day.isoformat()} is already logged and locked for editing "
                "(lock_submitted_days is enabled in the config)."
            )
            return render_template_string(
                ERROR_TEMPLATE,
                error=error,
                **_sidebar_context(config, storage, today, day, week_start_date),
            ), 403

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
            **_sidebar_context(config, storage, today, day, week_start_date),
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
  <script>
    (function () {
      var saved = localStorage.getItem('habit-theme');
      if (saved === 'light' || saved === 'dark') {
        document.documentElement.setAttribute('data-bs-theme', saved);
      }
    })();
  </script>
  <link rel="stylesheet"
        href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
  <link rel="stylesheet"
        href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined">
  <meta name="color-scheme" content="dark light">
  <style>
    :root {
      /* The raw Nord palette never changes between themes -- nord0 is always
         dark, nord8 always a pale frost blue. What changes per theme is which
         *role* (surface, text, accent) each one plays; see the --habit-*
         tokens below, which are the only things components should reference. */
      --nord0: #2e3440; --nord1: #3b4252; --nord2: #434c5e; --nord3: #4c566a;
      --nord4: #d8dee9; --nord6: #eceff4; --nord8: #88c0d0; --nord9: #81a1c1;
      --nord10: #5e81ac; --nord11: #bf616a; --nord13: #ebcb8b; --nord14: #a3be8c;

      --habit-bg: var(--nord0);
      --habit-surface: var(--nord1);
      --habit-surface-2: var(--nord2);
      --habit-border: var(--nord3);
      --habit-text: var(--nord6);
      --habit-text-muted: rgba(236, 239, 244, .65);
      --habit-accent: var(--nord8);
      --habit-accent-hover: var(--nord9);
      --habit-warn-text: var(--nord13);

      --bs-body-bg: var(--habit-bg);
      --bs-body-color: var(--habit-text);
      --bs-border-color: var(--habit-border);
    }
    :root[data-bs-theme="light"] {
      --habit-bg: #e5e9f0;
      --habit-surface: #ffffff;
      --habit-surface-2: #eceff4;
      --habit-border: #d8dee9;
      --habit-text: #2e3440;
      --habit-text-muted: rgba(46, 52, 64, .62);
      --habit-accent: var(--nord10);
      --habit-accent-hover: #4c6f96;
      --habit-warn-text: #8a5a12;
    }
    /* Left padding reserves room for the sidebar, a true full-height rail
       fixed to the viewport edge rather than laid out next to the card, so
       it stays put as the card scrolls/centers in the remaining space. */
    body { min-height: 100vh; padding: 3.5rem 1.25rem 3.5rem 14.5rem; background: var(--habit-bg); }
    .habit-shell { max-width: 42rem; margin: 0 auto; }
    .habit-sidebar {
      position: fixed; top: 0; left: 0; bottom: 0; width: 13rem;
      background: var(--habit-surface); border-right: 1px solid var(--habit-border);
      display: flex; flex-direction: column; overflow-y: auto;
    }
    .habit-logo {
      display: flex; align-items: center; gap: .5rem;
      padding: 1.1rem 1rem; text-decoration: none;
      color: var(--habit-text); font-weight: 700; font-size: 1.1rem;
      border-bottom: 1px solid var(--habit-border);
    }
    .habit-logo:hover { background: var(--habit-surface-2); }
    .habit-logo .material-symbols-outlined { color: var(--habit-accent); font-size: 1.4em; }
    .habit-theme-toggle {
      display: flex; align-items: center; justify-content: center; width: 100%;
      padding: .6rem 1rem; border: none; border-bottom: 1px solid var(--habit-border);
      background: none; color: var(--habit-text-muted); cursor: pointer;
    }
    .habit-theme-toggle:hover { background: var(--habit-surface-2); color: var(--habit-text); }
    .habit-theme-toggle .material-symbols-outlined { font-size: 1.3em; }
    .habit-week-nav {
      display: flex; align-items: center; justify-content: space-between;
      padding: .6rem .5rem; border-bottom: 1px solid var(--habit-border);
    }
    .habit-week-label { font-size: .8rem; color: var(--habit-text-muted); }
    .habit-week-arrow {
      display: flex; align-items: center; justify-content: center;
      width: 1.8rem; height: 1.8rem; border-radius: .4rem;
      color: var(--habit-text); text-decoration: none;
    }
    .habit-week-arrow:hover { background: var(--habit-surface-2); color: var(--habit-accent); }
    .habit-week-arrow.disabled { color: var(--habit-border); pointer-events: none; }
    .habit-stat { padding: .7rem 1rem; border-bottom: 1px solid var(--habit-border); }
    .habit-stat.highlight { background: rgba(136, 192, 208, .12); }
    .habit-stat-label {
      font-size: .68rem; color: var(--habit-text-muted);
      text-transform: uppercase; letter-spacing: .04em;
    }
    .habit-stat-value { font-size: 1.25rem; font-weight: 700; color: var(--habit-accent); }
    /* Flush, sharp-cornered blocks stacked with no gaps -- a ledger, not cards. */
    .habit-day {
      display: flex; flex-direction: column; justify-content: center; gap: .1rem;
      text-decoration: none;
      background: var(--habit-surface); border-bottom: 1px solid var(--habit-border);
      border-left: 3px solid transparent;
      padding: .65rem 1rem;
    }
    .habit-day:hover { background: var(--habit-surface-2); }
    .habit-day.today { background: rgba(136, 192, 208, .1); }
    .habit-day.selected { background: var(--habit-surface-2); border-left-color: var(--habit-accent); }
    .habit-day.future { opacity: .45; }
    .habit-day-date { font-size: .78rem; color: var(--habit-text-muted); }
    .habit-day-points { font-size: 1.05rem; font-weight: 600; color: var(--habit-text); }
    .habit-card {
      background: var(--habit-surface);
      border: 1px solid var(--habit-border);
      border-radius: 1rem;
      padding: 2.5rem;
    }
    .habit-title { font-weight: 700; color: var(--habit-text); margin-bottom: .35rem; }
    .habit-date-heading { color: var(--habit-text-muted); font-size: 1rem; margin: 0 0 .5rem; }
    .habit-total { font-size: 1.35rem; font-weight: 700; color: var(--habit-text); }
    .habit-total .accent { color: var(--habit-accent); }
    .habit-link { color: var(--habit-accent); text-decoration: underline; font-size: .9rem; }
    .habit-link:hover { color: var(--habit-accent-hover); }
    .form-text { font-size: .8rem; color: var(--habit-text-muted); }
    /* Goal prompts (not the date picker) get extra size/weight to stand out. */
    .habit-prompt { display: block; font-size: 1.08rem; font-weight: 600; color: var(--habit-text); margin-bottom: .6rem; }
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
      background: var(--habit-bg);
      border: 1px solid var(--habit-border);
      border-radius: .75rem;
      overflow: hidden;
    }
    .habit-field + .habit-field { margin-top: 1rem; }
    .habit-field-icon {
      display: flex; align-items: center; justify-content: center;
      flex: 0 0 3rem; font-size: 1.3em; color: var(--habit-accent);
      border-right: 1px dashed var(--habit-border);
    }
    .habit-field-body { flex: 1 1 auto; min-width: 0; padding: 1.1rem 1.2rem 1.1rem .9rem; }

    .form-control, .form-select {
      background-color: var(--habit-surface-2); border-color: var(--habit-border); color: var(--habit-text);
    }
    .form-control:focus, .form-select:focus {
      background-color: var(--habit-surface-2); border-color: var(--nord8); color: var(--habit-text);
      box-shadow: 0 0 0 .25rem rgba(136, 192, 208, .25);
    }
    .form-select {
      background-image: url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3e%3cpath fill='none' stroke='%23d8dee9' stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M2 5l6 6 6-6'/%3e%3c/svg%3e");
    }
    :root[data-bs-theme="light"] .form-select {
      background-image: url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3e%3cpath fill='none' stroke='%232e3440' stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M2 5l6 6 6-6'/%3e%3c/svg%3e");
    }
    .btn-primary {
      --bs-btn-color: var(--nord0); --bs-btn-bg: var(--nord8); --bs-btn-border-color: var(--nord8);
      --bs-btn-hover-color: var(--nord0); --bs-btn-hover-bg: #9fccd8; --bs-btn-hover-border-color: #9fccd8;
      --bs-btn-active-color: var(--nord0); --bs-btn-active-bg: var(--nord9); --bs-btn-active-border-color: var(--nord9);
      --bs-btn-focus-shadow-rgb: 136, 192, 208;
      --bs-btn-disabled-color: var(--nord0); --bs-btn-disabled-bg: var(--nord8); --bs-btn-disabled-border-color: var(--nord8);
    }
    .btn-outline-secondary {
      --bs-btn-color: var(--habit-text-muted); --bs-btn-border-color: var(--habit-border);
      --bs-btn-hover-color: var(--habit-text); --bs-btn-hover-bg: var(--habit-surface-2); --bs-btn-hover-border-color: var(--habit-border);
      --bs-btn-active-color: var(--habit-text); --bs-btn-active-bg: var(--habit-surface-2); --bs-btn-active-border-color: var(--habit-border);
    }
    /* Yes/No + segmented option buttons: unchecked outline, checked -> frost blue fill. */
    .btn-outline-primary {
      --bs-btn-color: var(--habit-text-muted); --bs-btn-border-color: var(--habit-border);
      --bs-btn-hover-color: var(--nord0); --bs-btn-hover-bg: var(--nord8); --bs-btn-hover-border-color: var(--nord8);
      --bs-btn-active-color: var(--nord0); --bs-btn-active-bg: var(--nord8); --bs-btn-active-border-color: var(--nord8);
      --bs-btn-focus-shadow-rgb: 136, 192, 208;
    }

    .table { --bs-table-color: var(--habit-text); --bs-table-border-color: var(--habit-border); }
    .table-hover > tbody > tr:hover > * { background-color: rgba(136, 192, 208, .08); color: var(--habit-text); }
    .text-muted { color: var(--habit-text-muted) !important; }

    .badge-nord-success { background: var(--nord14); color: var(--nord0); }
    .badge-nord-secondary { background: var(--nord3); color: var(--nord6); }
    .badge-nord-warning { background: var(--nord13); color: var(--nord0); }
    .badge-nord-danger { background: var(--nord11); color: var(--nord0); }

    .habit-alert {
      border-radius: .5rem; padding: 1rem; color: var(--habit-text);
    }
    .habit-alert-warn { background: rgba(235, 203, 139, .12); border: 1px solid var(--nord13); }
    .habit-alert-warn strong { color: var(--habit-warn-text); }
    .habit-alert-danger { background: rgba(191, 97, 106, .12); border: 1px solid var(--nord11); }
    .habit-alert-info { background: rgba(136, 192, 208, .12); border: 1px solid var(--nord8); }
    .habit-alert-info strong { color: var(--habit-accent); }

    /* This must stay the LAST rule block in the stylesheet: at equal
       specificity CSS resolves ties by source order, so any override in here
       needs to come after every base rule it overrides, not just the ones
       that happened to exist when this block was first written. Getting that
       wrong is exactly why .habit-card's mobile padding silently never
       applied -- the base .habit-card rule above used to sit after this
       block and kept winning. */
    @media (max-width: 900px) {
      /* Compact overall on small screens -- less padding everywhere, same
         font sizes. Bootstrap's spacing utilities (.mb-3 etc.) mark
         themselves !important, so overriding them takes !important too. */
      /* No horizontal padding: the sidebar rows sit flush against the
         viewport edges (that's the point of full-bleed rows on mobile), and
         .habit-card gets its own inset margin below instead of relying on
         body's padding -- which previously fought the header's full-bleed
         hack (a negative margin canceling out a padding it didn't own is
         fragile; not having the padding there at all is simpler). */
      body { padding: 0 0 1.25rem; }
      .habit-card { padding: .5rem; margin: 0 .75rem; }
      .habit-field-body { padding: .65rem .8rem .65rem .55rem; }
      .habit-field + .habit-field { margin-top: .6rem; }
      .habit-field-icon { flex-basis: 2.5rem; }
      .habit-title { margin-bottom: .2rem; }
      .mb-3 { margin-bottom: .6rem !important; }
      .mt-2 { margin-top: .4rem !important; }
      .mt-3 { margin-top: .6rem !important; }
      /* Three rows instead of one long strip -- but only the days row is
         meant to scroll. The header (logo/theme/all-time total) and week
         picker (nav arrows/label + this week's total) should always fully
         fit instead: their flexible child shrinks/truncates rather than the
         row overflowing, so there's exactly one scrollbar, on the days. */
      .habit-sidebar {
        position: static; width: 100%; height: auto;
        flex-direction: column; margin-bottom: .6rem;
      }
      .habit-sidebar-header, .habit-sidebar-week {
        display: flex; align-items: stretch; overflow: hidden;
        border-bottom: 1px solid var(--habit-border);
      }
      .habit-sidebar-days { display: flex; align-items: stretch; overflow-x: auto; }
      .habit-logo { border-bottom: none; border-right: 1px solid var(--habit-border); flex: 0 0 auto; padding: .4rem .6rem; font-size: 1rem; }
      .habit-theme-toggle {
        flex: 0 0 auto; width: auto; padding: .4rem .6rem;
        border-bottom: none; border-right: 1px solid var(--habit-border);
      }
      .habit-week-nav {
        flex: 1 1 auto; min-width: 0; align-items: center; gap: .4rem;
        border-bottom: none; padding: .35rem .5rem;
      }
      .habit-week-label {
        font-size: .72rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
      }
      .habit-week-arrow { flex: 0 0 auto; width: 1.5rem; height: 1.5rem; }
      /* The one flexible item per row -- shrinks (and truncates) instead of
         forcing the row to overflow and scroll. */
      .habit-stat {
        flex: 1 1 auto; min-width: 0; border-bottom: none;
        border-right: 1px solid var(--habit-border); padding: .35rem .6rem;
      }
      .habit-stat-label, .habit-stat-value {
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
      }
      .habit-stat-label { font-size: .62rem; }
      .habit-stat-value { font-size: 1.05rem; }
      /* Smaller day blocks on mobile -- tighter padding and type. */
      .habit-day {
        flex: 0 0 auto; min-width: 4.2rem; padding: .3rem .45rem;
        border-bottom: none; border-right: 1px solid var(--habit-border); border-left: none;
        border-top: 3px solid transparent;
      }
      .habit-day.selected { border-left: none; border-top-color: var(--habit-accent); }
      .habit-day-date { font-size: .7rem; }
      .habit-day-points { font-size: .92rem; }
    }
  </style>
"""

# The day rail, shared by all three pages (form/result/error) since the CSS
# above unconditionally reserves body padding for it -- without rendering it
# everywhere too, the other two pages would just show an empty gutter.
_SIDEBAR_HTML = """
  <nav class="habit-sidebar" aria-label="Recent days">
    <div class="habit-sidebar-header">
      <a href="/" class="habit-logo">
        <span class="material-symbols-outlined" aria-hidden="true">task_alt</span>
        <span>Habit</span>
      </a>
      <button type="button" class="habit-theme-toggle" id="theme-toggle" aria-label="Toggle light/dark theme">
        <span class="material-symbols-outlined" id="theme-toggle-icon" aria-hidden="true">dark_mode</span>
      </button>
      <script>
        (function () {
          var html = document.documentElement;
          var icon = document.getElementById('theme-toggle-icon');
          function sync() {
            icon.textContent = html.getAttribute('data-bs-theme') !== 'light' ? 'dark_mode' : 'light_mode';
          }
          document.getElementById('theme-toggle').addEventListener('click', function () {
            var next = html.getAttribute('data-bs-theme') === 'light' ? 'dark' : 'light';
            html.setAttribute('data-bs-theme', next);
            localStorage.setItem('habit-theme', next);
            sync();
          });
          sync();
        })();
      </script>
      {% if overall_total is not none %}
      <div class="habit-stat highlight">
        <div class="habit-stat-label">All-time total</div>
        <div class="habit-stat-value">{{ overall_total }} pts</div>
      </div>
      {% endif %}
    </div>
    <div class="habit-sidebar-week">
      {% if week_nav %}
      <div class="habit-week-nav">
        <a href="/?week={{ week_nav.prev }}" class="habit-week-arrow" aria-label="Previous week">
          <span class="material-symbols-outlined" aria-hidden="true">chevron_left</span>
        </a>
        <span class="habit-week-label">{{ week_nav.label }}</span>
        {% if week_nav.has_next %}
        <a href="/?week={{ week_nav.next }}" class="habit-week-arrow" aria-label="Next week">
          <span class="material-symbols-outlined" aria-hidden="true">chevron_right</span>
        </a>
        {% else %}
        <span class="habit-week-arrow disabled" aria-hidden="true">
          <span class="material-symbols-outlined">chevron_right</span>
        </span>
        {% endif %}
      </div>
      {% endif %}
      {% if overall_total is not none %}
      <div class="habit-stat">
        <div class="habit-stat-label">This week</div>
        <div class="habit-stat-value">{{ weekly_total }} pts</div>
      </div>
      {% endif %}
    </div>
    <div class="habit-sidebar-days">
      {% for d in sidebar_days %}
      <a href="/?date={{ d.date }}&week={{ week_start_date }}"
         class="habit-day{% if d.selected %} selected{% endif %}{% if d.is_today %} today{% endif %}{% if d.is_future %} future{% endif %}">
        <span class="habit-day-date">{{ d.label }}</span>
        <span class="habit-day-points">{% if d.points is not none %}{{ d.points }} pts{% else %}--{% endif %}</span>
      </a>
      {% endfor %}
    </div>
    <script>
      (function () {
        // Each day link is a normal full-page navigation, which would
        // otherwise reset this row's horizontal scroll (relevant on mobile,
        // where it's a scrollable strip) back to the start on every click.
        var el = document.querySelector('.habit-sidebar-days');
        if (!el) return;
        var key = 'habit-days-scroll';
        var saved = sessionStorage.getItem(key);
        if (saved !== null) el.scrollLeft = parseInt(saved, 10) || 0;
        el.addEventListener('scroll', function () {
          sessionStorage.setItem(key, el.scrollLeft);
        });
      })();
    </script>
  </nav>
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
  <div class="habit-shell">"""
    + _SIDEBAR_HTML
    + """
    <div class="habit-card">
      <h1 class="habit-title h3">{{ title }}</h1>
      <p class="habit-date-heading">{{ human_date }}</p>
      <a href="#" class="habit-link d-block mb-3" id="date-reveal">Logging a different day?</a>
      <div class="habit-total mb-3">Daily total: <span class="accent" id="habit-running-total">0 pts</span></div>
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
  <div class="habit-shell">"""
    + _SIDEBAR_HTML
    + """
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
  <div class="habit-shell">"""
    + _SIDEBAR_HTML
    + """
    <div class="habit-card">
      <h1 class="habit-title h3">Config error</h1>
      <div class="habit-alert habit-alert-danger"><pre class="mb-0">{{ error }}</pre></div>
    </div>
  </div>
</body>
</html>
"""
)
