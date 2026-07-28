# FastF1 Schedule Discovery and Season Backfill Planning

Status: **implemented**
Date: **2026-07-28**

## Purpose

This contract connects the accepted freshness policy to championship schedule
discovery and idempotent year-level job creation. It does not define REST
responses, UI behavior, manual cancellation, or sporting-data replacement.

## FastF1 Source Boundary

FastF1 3.8.3's public `EventSchedule` contains session start timestamps but drops
the session end timestamps required by archive eligibility. The implemented
loader therefore wraps FastF1 3.8.3's cache-decorated F1 timing season index,
which retains `StartDate`, `EndDate`, and `GmtOffset`.

This is an explicitly pinned upstream boundary:

- Historical years before 2018 are rejected.
- Testing events and round zero are excluded.
- Cache activation and schedule access use the same process-local serialization
  lock as full FastF1 session loading.
- FastF1 cache-version checks and HTTP request caching remain enabled.
- No estimated duration is invented when an end timestamp is missing.
- An incomplete or ambiguous snapshot fails before database writes.

The loader is isolated behind a protocol and controlled doubles in tests. No live
upstream request is part of the automated suite.

## Normalized Snapshot

The pure normalization layer produces immutable season, event, and session
records. It validates:

- Requested and loaded season identity.
- Positive, unique championship round numbers.
- At least one usable championship event and one usable session per event.
- Non-empty event and session names.
- Usable UTC start and end timestamps with end strictly after start.
- Unique canonical session keys within an event.
- At most the first five valid F1 sessions, matching FastF1 3.8.3 behavior.

Known keys are normalized to values such as `practice_1`, `qualifying`,
`sprint_qualifying`, `sprint_shootout`, `sprint`, and `race`. A future non-empty
session name receives a deterministic lowercase underscore key so a new format
does not require a PostgreSQL enum migration. The 2021–2022 historical
`Sprint Qualifying` name is normalized to `Sprint`, matching FastF1 3.8.3.

Event start and end values are the minimum session start and maximum session end
in the normalized event.

## Atomic Calendar Persistence

One successful schedule refresh:

1. Acquires a PostgreSQL transaction-level advisory lock scoped to the season.
2. Locks existing archive-owned calendar rows for that season.
3. Upserts the season by year, events by `(season_year, round_number)`, and
   sessions by `(event_id, session_key)`.
4. Rejects an event or session natural key already owned by a non-archive source.
5. Marks every row present in the snapshot with one PostgreSQL
   `last_discovered_at` timestamp.
6. Updates `coverage_checked_at` and `coverage_valid_until` only after the full
   snapshot is valid and persisted.
7. Commits calendar and coverage changes once.

Rows absent from a later snapshot are preserved because they can already be
referenced by jobs or sporting data. Their discovery marker remains older, so
they are excluded from future automatic job planning. This preserves historical
data without re-queuing a canceled or removed event/session.

Alembic revision `20260728_0003` adds nullable discovery markers and lookup
indexes to `events` and `sessions`. It invalidates existing non-null season
coverage during upgrade so the first request rebuilds authoritative discovery
membership. Downgrade removes only the new marker columns and indexes.

## Freshness and Job Planning

`ensure_season_backfill` performs upstream loading outside database locks, then
rechecks freshness under the season advisory lock. A concurrent caller may have
completed the refresh while loading; in that case the already fresh database
snapshot wins and the redundant loaded snapshot is discarded.

The planner:

- Skips the upstream schedule request while coverage is fresh.
- Evaluates only events and sessions marked by the latest successful discovery.
- Uses PostgreSQL time for coverage TTL, archive grace, and correction
  checkpoints.
- Excludes session-ingestion state owned by another source.
- Does not create an empty job around an unowned pending/running archive
  ingestion.
- Returns no new job when no session is eligible.

When sessions are eligible, the same advisory-locked transaction:

- Reuses the existing pending/running job for the season when one exists.
- Creates one new pending job only when no active job exists.
- Appends only missing `(job_id, session_id)` child rows.
- Preserves the request reason of a reused job.
- Uses `missing` for first coverage, `stale` for expired coverage or correction
  work, and `partial` for missing archive work under fresh coverage.

The existing partial unique index on active season jobs remains a database-level
defense in addition to the advisory lock.

## Failure Behavior

- Loader or normalization failure performs no calendar, coverage, or job write.
- Event/session source conflict rolls back the full refresh.
- A failed refresh never extends `coverage_valid_until`.
- Existing completed sporting data remains untouched and queryable.
- The worker remains independent: it processes the eligible child rows created
  by this planner and does not perform schedule discovery itself.
