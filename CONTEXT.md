# Habit — Progress Context (handoff)

A self-contained brief so a fresh agent (or a new Claude Code session) can pick
up this project cold. For the *design rationale* read [`ARCHITECTURE.md`](ARCHITECTURE.md);
for *user-facing usage* read [`README.md`](README.md). This file is the "where
things stand and what's next" layer on top of those.

Last updated after commit `83bd37f` (100 tests passing, pushed to `origin/main`).

## What Habit is

A configurable habit tracker. A user describes goals in a YAML file; the app
prompts them (daily check-in), scores each goal by its rules, and produces a
weekly recap. It's a **hybrid**: a deterministic Python core plus a scheduled
Claude agent that does only the natural-language work (rendering, judging fuzzy
entries, weekly reflection).

## Locked design decisions (full table in ARCHITECTURE.md)

- **Interaction:** scheduled agent posts a single structured daily prompt to
  Slack/chat; a web form is also being added as an entry surface.
- **Scoring:** hybrid — deterministic rules in code; agent fallback for fuzzy
  `judged` rules. **The LLM is never in the deterministic scoring path.**
- **Scoring model:** additive line items — base points + full-or-zero judged
  awards + (future) milestone streak bonuses; daily total = simple sum.
- **Data store:** `StorageAdapter` interface, BYO-db. JSON file and SQLite
  adapters are both built (picked by file extension); Google Sheets is the
  same interface, still deferred on service-account creds.
- **Data freshness:** raw answers are the source of truth (hand-editable);
  derived scores are a short-TTL cache, **recomputed on read**, never trusted as
  persisted state.
- **Config:** YAML in the repo, `extra="forbid"` (typos error out).
- **Interface:** core is a plain library; CLI/other wrappers are thin. Web UI
  uses Flask + Bootstrap 5 (Nord-inspired dark theme).
- **Scope:** single user now, but config-driven so users clone and bring their
  own goals/rules/db.
- **Deferred (pluggable):** scheduling runner (Claude cloud cron vs self-hosted)
  and where secrets live; MCP server over the library.

## What's implemented

### `habit.config` — config format + parser (Pydantic v2 + PyYAML)
- Models in `src/habit/config/models.py`:
  - `GoalType` = `bool` | `number` | `option`.
  - `Goal`: `name` (unique index key, no spaces `[A-Za-z0-9_-]`), `description`,
    `type`, `icon` (optional — emoji or Material Symbols name), `choices`
    (required for/exclusive to `option`), `shortcuts` (optional, number goals
    only, not on judged), `value: int | Rule`.
  - `Rule` = discriminated union on `type`: `ThresholdRule` (number),
    `OptionsRule` (option), `JudgedRule` (any; carries `judge` prompt + `points`).
  - `Config`: optional `title`/`user`/`timezone` + `goals`; `.by_name` index;
    unique non-empty names enforced. `title` drives the check-in page heading
    (`"<title> - Check-in"`), falling back to `user` then a generic heading.
- Loader `src/habit/config/loader.py`: `load(path)` / `loads(text)` → `Config`;
  all errors wrapped in `ConfigError` (`errors.py`).
- Cross-field validation: option needs choices; threshold only on number;
  options rule keys ⊆ choices; shortcuts only on plain number goals; etc.
- Extending: add a rule class + include it in the `Rule` union. **Scoring
  semantics live in the engine, not the config layer** (parser validates
  structure only).
- Canonical example: [`examples/habit.example.yaml`](examples/habit.example.yaml)
  (documents every field in its header comment).

### `habit.scoring` — the rule engine (pure, deterministic)
- `src/habit/scoring/engine.py`: `score_goal(goal, answer=None, verdict=None)`
  and `score_day(config, day) -> DayScore`.
- `src/habit/scoring/models.py`: `DayLog` (answers + verdicts, both from raw),
  `LineItem` (goal, points, `Status`, `detail`, optional `request`), `DayScore`
  (`.total`, `.pending`, `.by_goal`), `JudgeRequest`, `JudgeVerdict`, `Status`
  (`scored`/`skipped`/`pending_judgment`/`invalid`), `AnswerValue`.
- Flat-int semantics: award N if "done" — bool `True`, number `> 0`, any valid
  option choice (participation).
- `judged` rules do **not** call a model: with no verdict → `PENDING_JUDGMENT` +
  a `JudgeRequest`; with a verdict → full-or-zero, recorded with rationale/model.
- Missing answers → `SKIPPED`; malformed answers → `INVALID` (one bad cell never
  aborts the day). Pure → safe to recompute on read.

### `habit.storage` — the raw-answer persistence interface
- `src/habit/storage/base.py`: `StorageAdapter` (ABC) — `read_raw(since)`,
  `upsert_raw(day, answers)`, `write_scores(day, derived)`,
  `write_weekly(week, recap)` — matches the interface in ARCHITECTURE.md
  exactly. `RawDay` = `date` + `answers` dict.
- `src/habit/storage/json_file.py`: `JsonFileStorageAdapter` — one JSON file,
  keyed by ISO date under `raw`/`scores`/`weekly`, full-file rewrite per write.
- `src/habit/storage/sqlite.py`: `SqliteStorageAdapter` — stdlib `sqlite3`
  (`raw_days`/`scores`/`weekly` tables, upsert by date/week; answers and score
  payloads stored as JSON text in a column, so both adapters produce the exact
  same value shapes). A short-lived connection per call via a `_session()`
  context manager that both commits/rolls back *and* closes (bare
  `sqlite3.Connection` as a context manager only handles the transaction, not
  closing — worth remembering if this pattern gets copied elsewhere).
- `adapter_for_path(path)` in `storage/__init__.py`: picks the backend by
  extension (`.db`/`.sqlite`/`.sqlite3` → SQLite, else JSON). Used by both
  `create_app`'s default and the `habit-web` CLI's `--storage` flag, so there's
  one place that knows the dispatch rule.
- Neither adapter is concurrency-safe across multiple writers — fine for
  single-user local use. **Google Sheets adapter still not built** (needs
  service-account creds), but will drop in behind the same interface.
- Tests are conformance-style: a parametrized `adapter` fixture in
  `tests/test_storage.py` runs the shared behavioral tests against *both*
  backends, plus separate backend-specific tests that inspect the actual
  on-disk shape (JSON keys / SQLite rows) each one produces.

### `habit.web` — daily check-in form (Flask)
- `src/habit/web/app.py`: `create_app(config_path, storage=None)` +
  `build_fields(config)`. Defaults `storage` via `adapter_for_path` next to the
  config file (`<config_dir>/habit_data.json`) if not passed.
- **Form widgets**, chosen per goal by `_widget_for`: a Yes/No toggle-button
  pair for `bool` (`btn-check` radios styled as joined buttons — not a
  checkbox); a number input for `number` (plus a row of quick-value buttons if
  the goal declares `shortcuts`); joined toggle buttons for an `option` goal
  with ≤5 choices, a `<select>` dropdown past that; a textarea for any
  **judged** goal regardless of declared type. Every widget now goes through
  the same "blank → `SKIPPED`" parsing path (bool included — an untouched
  toggle is skipped, not silently `False`; an explicit "No" still scores 0).
- **Icons**: `goal.icon`, if set, renders in its own fixed-width column to the
  left of the field (flush against the panel background, dashed right border,
  vertically centered). A bare identifier (`^[a-z0-9_]+$`) is treated as a
  Material Symbols ligature name (loaded via Google Fonts CDN); anything else
  renders as a literal glyph (emoji).
- **Live running total**: a "Running total: N pts" readout at the top of the
  form, updated by JS on every field change. `_scoring_meta(goal)` mirrors just
  the *deterministic* arithmetic (flat bool/number/option, threshold,
  choice-points) as JSON embedded in the page (`#habit-scoring-meta`); judged
  goals can't be previewed client-side (no verdict exists yet) and show as
  `(+N pending)` instead. This is a UX preview only — `habit.scoring` is still
  the sole authority and recomputes the real total server-side on submit.
- **Date field**: defaults to today, hidden behind a "Logging a different
  day?" link (the input still carries today's value while hidden, so it still
  submits correctly if never revealed); once revealed, has its own
  Today/Yesterday quick-select. Quick-select buttons (date and number
  `shortcuts`) share one generic mechanism: `class="shortcut-btn"
  data-target="<input id>" data-value="<value>"`, and one script on the page
  discovers all such groups and wires them independently.
- **Theme**: Bootstrap 5 (CDN) reskinned with a Nord-inspired dark palette
  (`data-bs-theme="dark"` plus CSS-variable overrides on the specific
  components Bootstrap bakes literal colors into, e.g. `.btn-primary`'s
  `--bs-btn-bg`). Each goal field sits in its own visually separated panel
  (`.habit-field`) with a larger/bolder prompt (`.habit-prompt`); the date
  field's own label is deliberately left unemphasized.
- `GET /` renders the form (bad config → 400 error page).
- `POST /checkin` is **wired end-to-end**: parses the form into an answers
  dict, calls `storage.upsert_raw`, scores immediately with `score_day`, calls
  `storage.write_scores` (disposable cache), and renders a results page with
  per-goal status/detail/points, the total, and a list of judged goals still
  `pending_judgment`.
- Run: `uv run python -m habit.web --config habit.yaml --storage habit_data.json`
  (or `--storage habit_data.db` for SQLite; or the `habit-web` console script).
- Repeatedly verified with a real `uv run` server + `curl` (and small
  DOM-stubbed Node scripts for the client-side JS logic — shortcut buttons,
  running total, date reveal) in addition to the test suite.

### Tests (100 total)
`tests/test_config.py` (38), `tests/test_scoring.py` (22),
`tests/test_storage.py` (23, conformance + backend-specific), `tests/test_web.py`
(17). Convention: every feature ships with tests; keep them green.

## Environment & tooling

- **Runs on a remote Linux box** the user manages (`/home/daniel/GitHub/habit`,
  Python **3.14**). To view this in the Claude desktop app *with* a file pane,
  start an **SSH session** from the desktop Code tab pointed at this host/repo
  (the file pane works for local + SSH sessions, not cloud).
- **uv** manages env + lockfile: `uv sync`, `uv run pytest`. Change deps →
  `uv lock` and commit `uv.lock` (CI runs `--frozen`). uv is at `~/.local/bin`.
- **gh** CLI installed at `~/.local/bin`, authenticated as `Peaches491`.
  **SSH publickey auth to GitHub is currently broken** on this box (the
  `~/.ssh/agent` socket has no identities loaded — `id_rsa` exists but isn't
  added). Pushes in this session used `gh auth setup-git` + an explicit HTTPS
  remote URL (`git push https://github.com/Peaches491/habit.git main:main`)
  as a workaround; `origin` itself is still configured for SSH. Fix on the
  user's end: `ssh-add ~/.ssh/id_rsa` (or point `$SSH_AUTH_SOCK` at whatever
  agent normally holds it).
- Pip fallback still works (`pip install -e .`), but uv is primary.

## Git / CI state

- Remote: `git@github.com:Peaches491/habit.git`, branch **`main`**. All work
  so far has been committed directly to `main` (no feature branches).
- CI: `.github/workflows/ci.yml` — `astral-sh/setup-uv` + `uv run --frozen
  --python <ver> pytest` across Python 3.10–3.14 on every push/PR. Was green
  as of the last check. (Minor open cleanup, still unaddressed: bump
  `actions/checkout@v4`/`setup-uv@v5` to silence a Node 20 deprecation
  warning.)
- Commit convention: end messages with `Co-Authored-By: Claude Sonnet 5
  <noreply@anthropic.com>` (`git log` shows the format; earlier commits from
  prior sessions also carry a `Claude-Session` trailer this session didn't
  have a valid URL for, so it was omitted rather than guessed).

## Not built yet / deferred

- **Google Sheets storage adapter** — same `StorageAdapter` interface as the
  JSON/SQLite adapters; deferred on Google service-account creds.
- **Recording judge verdicts** — `JudgeVerdict`/`score_goal` support it, and
  the engine already reports `pending_judgment` line items with a
  `JudgeRequest`, but there's no `apply_judgment`-style write path yet (would
  need to feed a verdict back into a day's raw answers/verdicts and
  re-`upsert`/re-score). Needed before judged goals can ever leave `pending`.
- **Streaks & weekly bonuses** — need cross-day history *and* a new `streak`
  config rule type (parser doesn't have one).
- **Weekly recap + agent reflection.**
- **Agent front-of-house** — render prompt, run the judge loop over
  `DayScore.pending`, write weekly reflection; scheduling runner + secrets.

## Immediate next step

The user just asked to "build out a database abstraction to store the
results" — read narrowly, that's what this session just did (the SQLite
adapter alongside the existing JSON one, both behind `StorageAdapter`). If
there's a *further* ask hiding in that phrasing — e.g. actually persisting
`write_scores`/`write_weekly` output somewhere queryable for a future
dashboard, rather than treating them as pure write-only display caches — that
wasn't built and would be worth clarifying with the user rather than assuming.

Otherwise, the next natural seam is still closing the judged-goal loop (see
"Not built yet" above), or starting the Sheets adapter once creds exist.
