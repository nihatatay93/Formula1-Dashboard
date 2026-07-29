# Live Timing as a Separate Ephemeral Path

Status: **proposed**
Date: **2026-07-29**

## Purpose

This document defines live timing as a self-contained service that never writes
to the sporting-data schema. Live frames are appended to a disposable JSONL log,
served to clients over WebSocket, and deleted after a retention window. The
archive remains the only source of durable sporting data, reached through the
already-implemented FastF1 backfill path.

It replaces an earlier proposal in which live frames were stored as provisional
rows in the sporting-data tables and later reconciled against FastF1.

## Core Decision

**Nothing produced by live timing is ever promoted into PostgreSQL sporting
data.** A live session is a view onto a stream, not an ingestion phase.

After the session, FastF1 supplies the durable record through the existing
archive worker. Live and archive are two independent products that happen to
describe the same event, not two writers of one row.

## Why This Is Simpler

The provisional approach was constrained by two existing schema facts:

- `session_ingestions` keys on `session_id` alone, so a session can hold only
  one ingestion row and one `source`. Live and archive could not both hold
  state.
- `session_entries`, `results`, and `laps` enforce natural keys that exclude
  `source` and `record_state`, so a provisional live row and a finalized
  archive row cannot coexist on the same key.

Both constraints disappear here, because live data never occupies those keys.
No migration is required, and `record_state = 'provisional'` together with
`source = 'live_signalr'` simply remain unused by this design.

Reconciliation disappears with them. There is no finalization transaction, no
authority rule for disagreements, no orphan policy, and no provisional read
isolation in the historical endpoints.

One further consequence is worth stating: because the log is disposable,
**partial capture is not a correctness problem.** A user connecting at lap 30
yields a log starting at lap 30. That needs no gap tracking and no degraded
state, so the collector does not need to run continuously or predict session
starts.

## Upstream Boundary

The collector is isolated behind a protocol, as the FastF1 loader is, so the
automated suite runs against controlled doubles and never opens a live upstream
connection.

- Only the documented feed topics required for session state, entry identity,
  lap timing, and track status are consumed.
- Upstream frames are untrusted input. Unknown topics, unknown fields, and
  unparseable frames are counted and dropped.
- No credential, token, cookie, or session identifier is written to the JSONL
  log, to diagnostics, or to any client payload. Connection configuration is
  never logged alongside frames.
- Clients never reach SignalR. The browser talks only to the backend, which
  preserves the recorded client-isolation decision. Separating the live service
  does not mean exposing it to the browser.
- SignalR does not share the FastF1 request gate, rolling request ledger, or
  400/450 thresholds. Those measure cache-miss HTTP sends against a
  FastF1-specific limit and have no meaning for a streaming connection.

## Connection Lifecycle

A live session is started on demand when a user opens the live view, rather
than by a always-running collector.

- One collector owns at most one live session at a time.
- States are `disconnected → connecting → subscribed → streaming → stopped`.
- Reconnect reuses the existing equal-jitter backoff calculation. Live
  reconnects never consume any session's archive retry budget.
- On reconnect the collector requests the feed's current full state and rebuilds
  its in-memory view rather than assuming continuity. Because the log is
  append-only and disposable, a replayed frame is harmless: it is appended again
  and the in-memory view converges on the newer value.
- No resume token, sequence persistence, or gap ledger is required.

## Storage

Two representations, each with one job.

**Append-only JSONL log — the durable-enough record.** One file per live
session under a dedicated Docker volume:

```
live-sessions/{utc_date}__{event_slug}__{session_key}.jsonl
```

Each accepted frame appends one line:

```json
{"received_at":"2026-08-21T13:04:11.482Z","topic":"TimingData","seq":18422,"payload":{}}
```

- Writes are append-and-flush. No `fsync` per line: losing the last few lines to
  a hard crash is acceptable for disposable data, and the archive is
  authoritative regardless.
- A truncated final line on restart is dropped rather than repaired.
- The file is never rewritten, deduplicated, or compacted in place.
- A per-file size cap and a directory size cap are enforced. On breach the
  collector stops appending and marks the session log-degraded, but keeps
  streaming to clients. Filling the disk is a worse failure than losing a log.

**In-memory current view — what clients actually read.** The collector keeps the
latest state per topic and per driver so a connecting client receives an
immediate snapshot without replaying the file. On a collector restart during a
live session, this view is rebuilt by replaying the session's JSONL file.

No Redis and no temporary PostgreSQL tables. Redis would earn its place only for
restart survival across processes, multi-process fan-out, or TTL cleanup, none
of which apply to a single local collector; this keeps the recorded "No Redis at
the start" decision intact. Temporary PostgreSQL tables are rejected because
they would place disposable data back under Alembic and re-create the schema
coupling this design exists to avoid.

## Serving Clients

- A separate endpoint namespace, `/api/v1/live/...`, with its own WebSocket
  stream. Historical REST endpoints are untouched and continue to serve only
  finalized archive data.
- On connect a client receives the current snapshot, then incremental updates.
- The live UI is a separate view from the Session Workspace. It is not required
  to reuse the archive display components, and the two are expected to show
  different things: live favours positions, gaps, and sector state, while the
  archive view favours completed-session pace analysis.
- Live responses are explicitly marked as unconfirmed live data so a reader
  never mistakes them for the archive record.

## Retention and Cleanup

A periodic sweep inside the live service owns the directory it writes.

- The sweep runs at startup and on an interval, and deletes any session log
  whose modification time is older than a configured retention window.
- Retention defaults to 7 days and is configurable through an environment
  variable, following the existing validated-settings pattern in
  `app/ingestion/runtime_policy.py`.
- Deletion is unconditionally safe. A log older than the archive availability
  grace has already been superseded by the FastF1 backfill of the same session,
  and nothing in the application reads these files except live replay.
- The sweep never touches PostgreSQL and never inspects sporting data.

## Handoff to the Archive

There is no handoff to build. It already works.

`archive_availability_grace_seconds` defaults to `7200`
(`app/ingestion/runtime_policy.py`), and archive eligibility is
`scheduled_end_at + archive_availability_grace`
(`app/ingestion/freshness_policy.py`). The worker replans the current UTC season
at startup and every 15 minutes by default.

A session therefore becomes archive-eligible two hours after its scheduled end
and is backfilled by the existing worker with no live-specific code. Live data
is not promoted, not compared, and not consulted during that backfill.

The implemented replacement-safety guard that refuses to replace a session
containing non-archive sporting rows remains valid and simply never encounters a
live-owned row.

## Failure Behavior

- A collector crash loses at most the unflushed tail of one log. No PostgreSQL
  state is affected.
- An unavailable feed makes the live view unavailable. Archive backfill,
  historical endpoints, and the dashboard are unaffected.
- A full or unwritable log directory degrades logging only; streaming continues.
- No live failure can block, delay, or corrupt archive ingestion, because the
  two paths share no table, no lock, and no request budget.

## Explicitly Out of Scope

- No provisional sporting rows, and no use of `record_state = 'provisional'` or
  `source = 'live_signalr'` in the sporting-data tables.
- No change to `session_ingestions`, and no migration of any kind.
- No live-versus-archive comparison, and no durable record of feed
  disagreements. If that is wanted later, the JSONL logs are the input, and
  retention would need to be reconsidered first.
