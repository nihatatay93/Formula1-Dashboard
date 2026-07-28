# Formula1 Dashboard — First Five Milestones Execution Plan

This plan defines the approved implementation sequence for the
`feature/historical-analysis-telemetry` branch. The `main` branch remains
unchanged until the user explicitly decides otherwise.

Each milestone has an independent implementation, verification, documentation,
and Git commit boundary. Every commit uses a concise subject and an explanatory
body covering scope, rationale, and verification.

## Milestone 1 — Frontend Test Foundation

### Objective

Protect the implemented season archive and session-exploration workflows with
repeatable component and real-browser interaction coverage before adding new
product behavior.

### Implementation

1. Add Vitest, React Testing Library, `user-event`, `jest-dom`, and jsdom for
   frontend unit and component tests.
2. Add Playwright for deterministic browser interaction tests.
3. Add shared frontend test setup for DOM matchers and browser APIs used by the
   application.
4. Add typed API fixtures representing missing, active, completed, unavailable,
   and paginated historical states.
5. Cover the session workspace at component level:
   - loading and backend failure;
   - unavailable archive snapshot;
   - result rendering and participant selection;
   - first and subsequent lap pages;
   - snapshot-change restart behavior.
6. Cover primary dashboard workflows in a browser with network interception:
   - initial season load;
   - changing seasons;
   - starting an idempotent synchronization command;
   - polling job progress;
   - opening and closing a session workspace;
   - selecting a participant and loading another lap page.
7. Run browser coverage at desktop and mobile viewport sizes.
8. Keep tests independent from live FastF1 and deterministic local database
   contents.

### Acceptance Criteria

- Frontend unit/component tests pass.
- Browser interaction tests pass in Chromium.
- The production TypeScript/Vite build passes.
- Existing dashboard behavior and API contracts remain unchanged.
- Project context and verified commands are updated.

### Commit Boundary

One commit containing test dependencies, test configuration, fixtures,
component/browser tests, and documentation.

## Milestone 2 — Manual Selected-Lap Pace Analysis

### Objective

Allow a user to transparently choose representative laps and compare the
calculated pace of two session participants without claiming automatic
race-simulation detection.

### Implementation

1. Add pure TypeScript analysis functions for:
   - deterministic lap ordering;
   - selection eligibility;
   - arithmetic average lap time;
   - fastest and slowest selected laps;
   - selected-lap spread;
   - comparison delta between two selections.
2. Associate every selection with:
   - session ID;
   - session-entry ID;
   - selected lap numbers;
   - completed archive snapshot timestamp.
3. Add explicit lap selection controls to the existing lap table.
4. Preserve selections while moving between participants in the same session.
5. Limit the visible comparison workspace to two participant selections while
   allowing either slot to be cleared and replaced.
6. Display participant, team, selected-lap count, average, fastest, spread, and
   head-to-head average delta.
7. Mark deleted, inaccurate, untimed, pit-in, and pit-out facts visibly so the
   user understands the quality of a manual selection.
8. Clear incompatible selections if the session or completed archive snapshot
   changes.
9. Keep the first version ephemeral and browser-local; do not create saved
   analysis database tables or server-side aggregate endpoints.
10. Add pure-function, component, and browser interaction tests.

### Acceptance Criteria

- A user can select timed laps for one or two participants.
- Average calculations use only the explicitly selected laps.
- Comparison values are deterministic and correctly formatted.
- Snapshot replacement cannot silently combine old and new data.
- Unit/component/browser tests and the production build pass.
- Project context is updated.

### Commit Boundary

One commit containing analysis utilities, session-workspace UI, styles, tests,
and documentation.

## Milestone 3 — Telemetry Measurement and Storage Decision

### Objective

Measure FastF1 telemetry shape and volume with a reproducible tool before
selecting the first persistence strategy.

### Implementation

1. Add a read-only telemetry measurement utility that accepts an already loaded
   FastF1 telemetry frame and produces deterministic statistics:
   - sample count;
   - observed duration;
   - sampling interval distribution;
   - channel nullability;
   - in-memory byte size;
   - estimated relational row size and session/season projections.
2. Add a controlled measurement command that uses the persistent FastF1 cache,
   the existing serialized access boundary, and the shared request-budget
   accounting when real cache misses occur.
3. Measure representative practice, qualifying, and race laps when cached
   source data is available without bypassing request controls.
4. Record raw measurement inputs and summarized results without storing
   credentials, cookies, raw upstream payloads, or machine-specific cache
   paths.
5. Compare standard PostgreSQL and TimescaleDB against the measured access
   pattern.
6. Decide the first implementation target:
   - standard PostgreSQL for explicitly requested lap-scoped telemetry;
   - normalized relational rows with bounded keyset reads;
   - retention of a future TimescaleDB migration path if measured scale or
     query patterns later justify it.
7. Add deterministic tests for measurement calculations and malformed input.

### Acceptance Criteria

- The measurement utility and command are reproducible.
- Measurement calculations are covered without network access.
- Representative evidence is recorded where available.
- The PostgreSQL/TimescaleDB decision has rationale, constraints, and a review
  trigger.
- Backend lint and tests pass.
- Project context is updated.

### Commit Boundary

One commit containing the measurement utility, tests, evidence report, storage
decision, and documentation.

## Milestone 4 — Bounded Historical Telemetry

### Objective

Provide idempotent, lap-scoped historical telemetry ingestion and bounded REST
reads without loading or returning season-wide telemetry.

### Database Design

1. Add a telemetry-ingestion state row per stored lap:
   - `pending`, `running`, `completed`, or `failed`;
   - attempt and lifecycle timestamps;
   - heartbeat and retry eligibility;
   - sanitized error code/message;
   - completed sporting-snapshot timestamp.
2. Add normalized lap telemetry samples:
   - lap identity and deterministic sample index;
   - lap-relative and session-relative time;
   - distance and relative distance;
   - speed, RPM, gear, throttle, brake, and DRS;
   - X/Y/Z position where available;
   - source and record state.
3. Enforce one sample index per lap, non-negative ranges, bounded channel
   values, source/state checks, and indexes for lap-keyset access.
4. Cascade telemetry rows only when their owning lap is removed; continue to
   restrict unrelated sporting-data deletion.

### Ingestion Design

1. Add a pure FastF1 telemetry normalization boundary.
2. Load telemetry through the persistent serialized FastF1 cache and shared
   request budget.
3. Resolve the requested database session entry and lap back to an exact
   season, round, session, driver/car, and lap request.
4. Queue one idempotent telemetry request per lap from the API.
5. Extend the existing PostgreSQL-backed worker to claim one telemetry request
   only when no archive session is claimable.
6. Persist one normalized lap atomically, replace stale samples, and bind the
   completed telemetry to the current sporting snapshot.
7. Apply bounded retry, heartbeat, lease recovery, ownership fencing, and
   sanitized failures.

### REST Contract

1. Add an idempotent lap-telemetry command endpoint.
2. Add a read-only lap-telemetry endpoint with:
   - current ingestion state;
   - snapshot compatibility;
   - sample count;
   - sample-index keyset pagination;
   - a conservative default and hard maximum page size;
   - `Cache-Control: no-store`;
   - stable client-safe errors.
3. Keep telemetry absent from season, session-detail, result, and lap-summary
   responses.

### Acceptance Criteria

- Migration upgrade, downgrade, re-upgrade, and metadata drift checks pass.
- Duplicate commands converge on one lap request.
- Persistence is idempotent and replaces stale samples atomically.
- Stale sporting snapshots are detected.
- Worker ownership and recovery are tested.
- REST validation, pagination, errors, and OpenAPI are tested.
- Full backend lint/tests, frontend build, and Compose parsing pass.
- Project context is updated.

### Commit Boundary

One commit containing the migration, models, normalization/loading,
orchestration, API, tests, and documentation.

## Milestone 5 — Automatic Current-Season Planning

### Objective

Make newly available current-season sessions and correction checkpoints
discoverable without depending indefinitely on a manual dashboard command.

### Database and Read Model

1. Persist deferred current-season events discovered from the curated schedule,
   keyed by season and round.
2. Associate deferred membership with the successful coverage snapshot.
3. Atomically replace current deferred membership during schedule refresh while
   preserving historical calendar and sporting rows.
4. Include current deferred events in the read-only season overview so repeated
   reads do not depend on the response from the command that performed refresh.

### Worker Scheduling

1. Add validated automatic-planning settings:
   - enabled flag;
   - bounded planning interval;
   - current UTC season selection.
2. Run current-season planning at worker startup and periodically afterward.
3. Reuse the existing coverage TTL, correction eligibility, advisory lock,
   request budget, active-job uniqueness, and idempotent job planner.
4. Keep planning failures secret-safe and non-fatal to normal archive and
   telemetry work.
5. Avoid overlapping automatic planner executions and stop scheduling new work
   during graceful shutdown.

### Dashboard Behavior

1. Read persisted deferred events from the season overview.
2. Explain future events that are waiting for exact timing metadata without
   requiring a recent manual synchronization response.
3. Preserve the manual Check & Sync command as an explicit refresh control.

### Acceptance Criteria

- Current-season planning occurs at startup and at the configured interval.
- Fresh coverage checks do not make unnecessary upstream calls.
- Newly eligible archive correction or missing-session work is queued
  idempotently.
- Deferred-event membership persists and is removed when exact sessions become
  available.
- Planner failures do not stop the worker loop.
- Migration lifecycle, backend tests/lint, frontend tests/build, browser tests,
  and Compose configuration pass.
- Project context is updated.

### Commit Boundary

One commit containing deferred-event persistence, automatic worker planning,
season API/dashboard updates, tests, and documentation.

## Final Cross-Milestone Verification

After the fifth milestone:

1. Confirm the active branch is `feature/historical-analysis-telemetry`.
2. Confirm `main` still points to its original commit.
3. Run backend Ruff and the complete backend test suite against an isolated,
   migrated PostgreSQL 17 database.
4. Run frontend unit/component tests, browser tests, and production build.
5. Run `docker compose config --quiet`.
6. Run Alembic head and model/schema drift checks.
7. Inspect `git status`, the five milestone commits, and the explanatory commit
   bodies.
8. Do not push any branch.
