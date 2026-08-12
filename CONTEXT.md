# Habit — Progress Context (handoff)

A self-contained brief so a fresh agent (or a new Claude Code session) can pick
up this project cold. For the *design rationale* read [`ARCHITECTURE.md`](ARCHITECTURE.md);
for *user-facing usage* read [`README.md`](README.md). This file is the "where
things stand and what's next" layer on top of those.

Last updated after commit `36c2532`, with `POST /checkin` now wired to scoring
+ a JSON-file storage adapter (68 tests passing; CI green on `main` as of the
prior commit — this session's changes are uncommitted, see below).

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
- **Data store:** Google Sheets behind a `StorageAdapter` interface (BYO-db:
  SQLite/Postgres later). *Not built yet.*
- **Data freshness:** raw answers are the source of truth (hand-editable);
  derived scores are a short-TTL cache, **recomputed on read**, never trusted as
  persisted state.
- **Config:** YAML in the repo, `extra="forbid"` (typos error out).
- **Interface:** core is a plain library; CLI/other wrappers are thin. Web UI
  uses Flask.
- **Scope:** single user now, but config-driven so users clone and bring their
  own goals/rules/db.
- **Deferred (pluggable):** scheduling runner (Claude cloud cron vs self-hosted)
  and where secrets live; MCP server over the library.

## What's implemented

### `habit.config` — config format + parser (Pydantic v2 + PyYAML)
- Models in `src/habit/config/models.py`:
  - `GoalType` = `bool` | `number` | `option`.
  - `Goal`: `name` (unique index key, no spaces `[A-Za-z0-9_-]`), `description`,
    `type`, `choices` (required for/exclusive to `option`), `value: int | Rule`.
  - `Rule` = discriminated union on `type`: `ThresholdRule` (number),
    `OptionsRule` (option), `JudgedRule` (any; carries `judge` prompt + `points`).
  - `Config`: optional `user`/`timezone` + `goals`; `.by_name` index; unique
    non-empty names enforced.
- Loader `src/habit/config/loader.py`: `load(path)` / `loads(text)` → `Config`;
  all errors wrapped in `ConfigError` (`errors.py`).
- Cross-field validation: option needs choices; threshold only on number;
  options rule keys ⊆ choices; etc.
- Extending: add a rule class + include it in the `Rule` union. **Scoring
  semantics live in the engine, not the config layer** (parser validates
  structure only).

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
- `src/habit/storage/json_file.py`: `JsonFileStorageAdapter` — a local,
  credential-free adapter (one JSON file, keyed by ISO date under
  `raw`/`scores`/`weekly`). Built to unblock the web UI now; **Google Sheets
  adapter is still not built** (needs service-account creds), but will drop in
  behind the same `StorageAdapter` interface without callers changing.
- Not concurrency-safe across multiple writers — fine for single-user local use.

### `habit.web` — daily check-in form (Flask)
- `src/habit/web/app.py`: `create_app(config_path, storage=None)` +
  `build_fields(config)`. Defaults `storage` to a `JsonFileStorageAdapter`
  next to the config file (`<config_dir>/habit_data.json`) if not passed.
- Form auto-generated from goals: checkbox (`bool`), number input (`number`),
  `<select>` (`option`), textarea (any **judged** goal — free text). Plus a date
  field (backfill) and per-goal points hints. Config reloads per request.
- `GET /` renders the form (bad config → 400 error page).
- `POST /checkin` is **wired end-to-end**: parses the form into an answers
  dict (checkbox absent → `False`, not skipped; number that fails to parse is
  passed through as a raw string so the engine flags it `INVALID` rather than
  dropping it; blank fields are omitted → `SKIPPED`), calls
  `storage.upsert_raw`, scores immediately with `score_day`, calls
  `storage.write_scores` (disposable cache), and renders a results page with
  per-goal status/detail/points, the total, and a list of judged goals still
  `pending_judgment`.
- Run: `uv run python -m habit.web --config habit.yaml --storage habit_data.json`
  (or `habit-web` script). New `--storage` flag (default `$HABIT_STORAGE` or
  `habit_data.json`), mirroring `--config`.
- Verified with a real `uv run` server + `curl` round trip (GET form, POST a
  full check-in, inspected the resulting JSON file) in addition to the test
  suite — see commit for the smoke-test transcript if needed.

### Tests
`tests/test_config.py` (31), `tests/test_scoring.py` (22), `tests/test_storage.py`
(6), `tests/test_web.py` (9, updated from the old stub-echo test to real
scoring/storage assertions). 68 total passing.
Convention: every feature ships with tests; keep them green.

## Environment & tooling

- **Runs on a remote Linux box** the user manages (`/home/daniel/GitHub/habit`,
  Python **3.14**). To view this in the Claude desktop app *with* a file pane,
  start an **SSH session** from the desktop Code tab pointed at this host/repo
  (the file pane works for local + SSH sessions, not cloud).
- **uv** manages env + lockfile: `uv sync`, `uv run pytest`. Change deps →
  `uv lock` and commit `uv.lock` (CI runs `--frozen`). uv is at `~/.local/bin`.
- **gh** CLI installed at `~/.local/bin`, authenticated as `Peaches491` (SSH).
- Pip fallback still works (`pip install -e .`), but uv is primary.

## Git / CI state

- Remote: `git@github.com:Peaches491/habit.git` (SSH), branch **`main`**.
- CI: `.github/workflows/ci.yml` — `astral-sh/setup-uv` + `uv run --frozen
  --python <ver> pytest` across Python 3.10–3.14 on every push/PR. Currently
  green. (Minor open cleanup: bump `actions/checkout@v4`/`setup-uv@v5` to silence
  a Node 20 deprecation warning.)
- Commit convention: end messages with the `Co-Authored-By` + `Claude-Session`
  trailers already used on existing commits (`git log` shows the format).

## Not built yet / deferred

- **Google Sheets storage adapter** — the `StorageAdapter` interface and a
  local JSON-file implementation now exist (see above); Sheets is the same
  interface, still deferred on Google service-account creds.
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

With `POST /checkin` and the JSON storage adapter both wired up (this
session), the next natural seam is closing the judged-goal loop: a way to feed
an agent's `JudgeVerdict` back for a `pending_judgment` goal (e.g. a small
`apply_judgment(storage, config, day, goal, verdict)` helper: read the day's
raw answers back out via `read_raw`, re-score with the verdict included, and
`write_scores` again) — otherwise judged goals can never resolve past
`pending`. That, or start the Sheets adapter once creds are available,
whichever the user wants to unblock first.

**This session's changes are uncommitted** — review with `git status`/`git
diff` and commit when ready.
