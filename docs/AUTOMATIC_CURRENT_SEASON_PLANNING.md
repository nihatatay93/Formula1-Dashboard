# Automatic Current-Season Planning

## Purpose

The archive worker periodically plans the current UTC season so newly
published FastF1 session boundaries and due archive-correction checkpoints do
not depend on a dashboard user pressing **Check & Sync**.

This is a planning loop only. It reuses the existing season planner and leaves
archive ingestion to the normal single-concurrency worker path.

## Runtime Policy

Automatic planning is enabled by default and configured through:

- `AUTOMATIC_CURRENT_SEASON_PLANNING_ENABLED`: `true` or `false`; defaults to
  `true`.
- `AUTOMATIC_CURRENT_SEASON_PLANNING_INTERVAL_SECONDS`: planning cadence;
  defaults to `900` seconds and must be between `60` and `21600` seconds.

The worker derives the target year from an aware UTC timestamp. It runs the
planner once at worker startup and again after each configured interval. The
planner runs synchronously in the worker loop, so two automatic runs cannot
overlap.

The automatic call uses the same behavior as the manual backfill command:

- the current-season coverage TTL determines whether FastF1 schedule discovery
  is required;
- the existing PostgreSQL advisory lock serializes planning for the season;
- the active-job partial unique index prevents two active jobs for one season;
- correction-checkpoint and archive-grace eligibility remain centralized in
  the existing freshness policy;
- schedule cache misses share the PostgreSQL FastF1 request ledger and request
  budget.

Planner failures are non-fatal to the worker. Logs contain the operation and
exception type only, without exception text that could contain upstream or
credential data. A failed run is retried at the next interval. When shutdown is
requested during planning, the worker does not begin maintenance or claim new
archive or telemetry work afterward.

## Deferred Future Events

FastF1 can publish a current-season event before exact session timing
boundaries are available. These events cannot be queued safely, but available
earlier events must still proceed.

Alembic Revision 7 adds `deferred_season_events`:

- composite primary key `(season_year, round_number)`;
- foreign key to `seasons.year` with cascade deletion;
- public event name and scheduled event start;
- `discovered_at`, matching the successful season coverage snapshot;
- non-empty event-name and positive-round checks;
- lookup index `(season_year, discovered_at, round_number)`.

A successful schedule refresh atomically upserts the current deferred set and
removes deferred rows no longer present in that snapshot. The season planner
and read-only season overview return only rows whose `discovered_at` matches
`seasons.coverage_checked_at`. This prevents a preserved older row from being
presented as part of the latest coverage snapshot.

The dashboard renders the persisted deferred-event notice from
`GET /api/v1/seasons/{season_year}`. The notice therefore survives page reloads
and fresh manual checks that correctly reuse unexpired coverage. **Check &
Sync** remains available as an explicit user command.

## Verification

The implementation is covered by:

- runtime setting default, environment parsing, and boundary tests;
- startup, periodic, disabled, failure, no-overlap, and shutdown worker tests;
- PostgreSQL integration coverage for missing and correction-due sessions,
  idempotent repeated planning, and active-job uniqueness;
- schedule-refresh replacement and fresh-coverage reuse tests;
- season-overview latest-snapshot deferred-event tests;
- Revision 7 metadata, upgrade, downgrade, re-upgrade, and drift checks;
- dashboard component/build and desktop/mobile Playwright acceptance tests.
