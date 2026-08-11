# Habit — Architecture (draft)

A habit tracker that prompts you daily, awards points by configurable rules, and
produces a weekly recap. Hybrid of deterministic **code** and a scheduled
**Claude agent**.

Status: design draft. Not yet built.

## Decisions locked

| Area | Decision |
|------|----------|
| Interaction | Scheduled agent posts a **single structured daily prompt** to Slack/chat; user replies once |
| Scoring | **Hybrid** — deterministic rules in code, agent fallback for fuzzy "judge" rules |
| Data store | **Google Sheets**, behind a storage-adapter interface (SQLite/Postgres later) |
| Scope | Single user now, but **config-driven** so users bring their own goals/rules/db |
| Rule authoring | **Declarative config** (YAML): points + a rule; fuzzy rules carry a natural-language judge prompt |
| Weekly | **Recap/report** + **agent reflection/coaching** (no separate weekly *habits*) |
| Execution | **Core Python library/CLI** (deterministic, testable) + **agent front-of-house** |
| Stack | Python |
| Config location | **YAML in the repo** — rules are the "program", so versioned & code-reviewed |
| Agent↔core | Core is a plain **library**; **CLI wrapper first**, MCP later |
| Scoring model | **Additive line items** — base points + full-or-zero judged awards + milestone streak bonuses; daily total = simple sum |
| Corrections | **Hand-edits + agent backfill**, both allowed |
| Data freshness | **Raw answers are source of truth; derived scores are a short-TTL cache** (recompute on read, never stale > a few minutes) |
| Scheduling | Trigger is **pluggable**; pick the runner (Claude cloud cron vs self-hosted) later |

## Core principles

**1. The LLM is never in the deterministic scoring path.** Plain code owns data,
deterministic rules, and prompt assembly — every awarded point has a traceable
rule + input. The agent owns only natural-language production: rendering the
prompt, judging fuzzy entries, and writing the weekly reflection. Judged awards
are recorded *with their rationale and the model used*, so even fuzzy scores are
auditable in the sheet.

**2. Raw answers are the source of truth; derived scores are a cache.** Because
the Sheet is hand-editable at any time, stored points/streaks can go stale.
Derived values are recomputed on read from the raw answers and never trusted as
persisted state (short TTL, "never cache more than a few minutes"). A recompute
pass is a first-class core operation, run on every daily/weekly job and on
demand. Materialized score columns in the Sheet exist only for human display and
are treated as a disposable cache.

## Components

```
              ┌─────────────────────────── Claude agent (front-of-house) ──────────────────────────┐
              │  renders prompt · posts to Slack · JUDGES fuzzy entries · writes weekly reflection  │
              └───────────────┬───────────────────────────────────────────────────┬────────────────┘
                              │ calls core as tools (CLI/MCP)                       │
              ┌───────────────▼───────────────────────────────────────────────────▼────────────────┐
              │                              CORE  (Python library/CLI)                              │
              │   config loader · deterministic rule engine · scoring · prompt assembly · weekly recap│
              └───────────────┬───────────────────────────────────────────────────┬────────────────┘
                              │ StorageAdapter interface                            │
                    ┌─────────▼─────────┐                                 ┌─────────▼─────────┐
                    │  Sheets adapter   │  (adapter #1)                   │  Config (YAML)    │
                    │  log + history    │                                 │  habits/rules/pts │
                    └───────────────────┘                                 └───────────────────┘
```

### Core (Python)
- **Config loader** — parses & validates the per-user habit/rule config.
- **Rule engine** — evaluates deterministic rules; flags `judged` rules as a
  queue for the agent; applies verdicts the agent returns.
- **Scoring** — turns rule results into awarded points with a trace.
- **Prompt assembly** — builds the day's check-in text from config (data, not prose).
- **Weekly recap** — pure aggregation: points, streaks kept/broken, best/worst.
- **StorageAdapter** — the only thing that knows about Sheets.

### Agent (scheduled)
- Calls `render_daily` → posts to Slack → collects the reply.
- Calls `record_day` (validate + write + deterministic scoring).
- For each `judged` item: reads entry + judge prompt, produces a verdict,
  calls `apply_judgment`.
- Weekly: calls `weekly_recap`, then writes the reflection/coaching narrative.

## Two contracts worth pinning now

### 1. Config schema (per user)
```yaml
user: daniel
timezone: America/Chicago
habits:
  - id: exercise
    prompt: "Did you exercise today?"
    points: 10
    rule: { type: boolean }              # deterministic

  - id: gym_3x
    prompt: null                         # not asked directly; derived
    points: 20
    rule: { type: weekly_count, of: exercise, at_least: 3 }   # evaluated on rollup

  - id: journaling
    prompt: "What did you write about today?"
    points: 15
    rule:
      type: judged                        # agent fallback
      judge: "Award if the entry shows genuine reflection, not a token 'nothing'."
```
Rule `type`s (deterministic unless noted): `boolean`, `count`/`threshold`,
`streak_bonus`, `weekly_count`, and `judged` (agent).

### 2. StorageAdapter (Python interface)
The adapter reads/writes **raw** answers only; scoring is derived, never stored
as truth. (Config is a separate loader — YAML today, another source later.)
```
read_raw(since)          -> list[RawDay]     # authoritative, hand-editable
upsert_raw(date, answers)-> None             # idempotent by date (backfill/amend)
write_scores(day, derived)-> None            # display cache only, disposable
write_weekly(week, recap)-> None             # display cache only, disposable
```
A `RawDay` is just: date + raw answers (source of truth). Derived scoring — per
-habit rule result, points, streak state, and for judged habits the verdict +
rationale + model — is produced by the recompute pass and only *materialized*
into the Sheet for human display.

## Data flow

**Daily:** cron → agent renders prompt → Slack → user replies → `record_day`
(`upsert_raw` + recompute, idempotent) → agent judges fuzzy items →
`apply_judgment` (records verdict into raw, recompute) → confirmation to Slack.

**Weekly:** cron → recompute from raw → `weekly_recap` (aggregation) → agent
writes reflection → `write_weekly` → posted to Slack.

**Recompute** (first-class, run on every job + on demand): read raw → evaluate
deterministic rules → collect judged results already recorded in raw → sum
additive line items → refresh the display cache. Never reads stale derived state.

## Resolved

- **Config:** YAML in the repo, loaded/validated by the core.
- **Interface:** core is a library; ship a **CLI wrapper first**, MCP later.
- **Scoring:** additive line items — base points, full-or-zero judged awards,
  milestone streak bonuses added on top; daily total is a plain sum.
- **Corrections:** hand-edits *and* agent backfill both supported; raw is
  authoritative, derived scores recomputed on read (short TTL).

## Deferred (kept pluggable)

1. **Scheduling runner.** Claude cloud cron (no server) vs self-hosted
   (GitHub Actions / VM / home box). Behind the trigger abstraction.
2. **Secrets.** Google service-account creds + Slack + Anthropic keys — follows
   from the runner choice (`.env` self-hosted, secret store for cloud).
3. **Interface upgrade.** When/whether to add the MCP server over the library.
```
