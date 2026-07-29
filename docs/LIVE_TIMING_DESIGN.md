# SignalR Live Timing Boundary, Provisional Storage, and Finalization

Status: **proposed**
Date: **2026-07-29**

## Purpose

This document defines the upstream SignalR boundary, connection lifecycle,
deduplication rules, provisional storage model, and FastF1 reconciliation rules
that must be agreed before live ingestion is implemented. It is the design half
of `Next Steps` item 1.

It does not define WebSocket payload schemas for the dashboard, live UI
behavior, or the iOS client. Those follow the storage and finalization contract
agreed here.

## Existing Constraints This Design Must Respect

The database already anticipates live timing, and two existing constraints
decide most of the design:

- `SOURCES` already contains `live_signalr` and `RECORD_STATES` already contains
  `provisional`. No enum migration is required to store live-owned rows.
- `session_ingestions` uses `session_id` alone as its primary key. **One session
  can hold only one ingestion row, carrying one `source`.** Archive ingestion
  already rejects rows owned by another source and excludes them from planning.
- `session_entries`, `results`, and `laps` enforce natural keys that exclude
  `source` and `record_state`: `(session_id, entry_key)`,
  `session_entry_id`, and `(session_entry_id, lap_number)`. **A provisional live
  row and a finalized archive row cannot coexist on the same natural key.**

The consequence is that live and archive ingestion are not two independent
writers of the same session. They are two phases of one session's lifecycle, and
the handover between them must be explicit.

## Upstream Boundary

The live collector is isolated behind a protocol, exactly as the FastF1 loader
is, so the automated suite runs against controlled doubles and never opens a
live upstream connection.

The boundary is explicitly pinned:

- The collector consumes only the documented live timing feed topics required
  for session state, entry identity, lap timing, and track status.
- Raw upstream frames are treated as untrusted input. Unknown topics, unknown
  fields, and unparseable frames are counted and dropped, never persisted
  as sporting data.
- No credential, token, cookie, or session identifier is written to logs,
  diagnostics, database rows, or documentation.
- The collector never serves clients directly. Dashboard and iOS clients reach
  live data only through the backend, preserving the recorded client isolation
  decision.
- SignalR is a separate upstream from FastF1. It does not share the FastF1
  request gate, the rolling request ledger, or the 400/450 thresholds, because
  those measure cache-miss HTTP sends against a FastF1-specific limit. Live
  connection attempts get their own counters and their own backoff.

## Connection Lifecycle, Reconnect, and Resume

Live timing is a long-lived streaming connection, which does not fit the
existing single-concurrency claim/heartbeat worker. It runs as a separate
process.

- One collector process owns at most one live session at a time.
- Ownership is claimed through the same PostgreSQL advisory-lock and fencing
  primitives already used for archive claims, so two collectors cannot both
  write one session.
- The connection state machine is `disconnected → connecting → subscribed →
  streaming → draining`. Only `streaming` may write sporting data.
- Reconnect uses the existing equal-jitter backoff calculation. Repeated
  connect failures do not consume a session's archive retry budget, because the
  two budgets describe different upstreams.
- On reconnect the collector does **not** assume continuity. It requests the
  feed's current full state, rebuilds its in-memory view, and reconciles that
  view against already-persisted provisional rows before resuming incremental
  writes.
- A resumed connection that cannot obtain a coherent full state transitions the
  session to a recorded live-degraded condition rather than writing a partial
  view over good data.

## Deduplication

The feed replays and re-sends. Deduplication is defined at two levels.

**Frame level.** Each accepted frame carries a monotonic per-topic sequence
derived from the feed. The collector keeps the last applied sequence per topic
in memory and discards frames at or below it. This is process-local and is
rebuilt from the full state after reconnect, so it never needs persistence.

**Row level.** Frame-level dedup is not sufficient, because a reconnect can
legitimately replay content the previous connection already persisted.
Persistence is therefore idempotent on the existing natural keys: a provisional
lap upserts on `(session_entry_id, lap_number)`, a provisional entry upserts on
`(session_id, entry_key)`. Re-applying a frame rewrites the same row rather than
inserting a duplicate.

A provisional row is only overwritten by a later observation of the same natural
key. Live data never deletes rows it did not write.

## Provisional Storage

Provisional live rows use the existing sporting-data tables with
`source = 'live_signalr'` and `record_state = 'provisional'`. No parallel live
table set is introduced, for three reasons: the natural keys already forbid
coexistence, the read API would otherwise need to union two shapes, and
finalization would become a cross-table migration rather than a state change.

Two schema changes are required.

1. **Session ingestion ownership.** `session_ingestions` cannot express "live
   ingestion finished, archive ingestion is now due" while its primary key is
   `session_id` alone. The recommended change is to make the row's `source`
   part of its identity — primary key `(session_id, source)` — so a session may
   hold one live row and one archive row across its lifecycle, and the archive
   planner's existing "owned by another source" exclusion keeps working
   unchanged. The alternative, reusing the single row and rewriting its
   `source` at handover, destroys the live attempt's history and its failure
   diagnostics, and is not recommended.

2. **Provisional read isolation.** Existing historical endpoints must not begin
   returning provisional rows, or a completed-session response could silently
   change shape mid-session. Read services filter on
   `record_state = 'finalized'` explicitly rather than relying on the absence of
   live data.

The existing `deleted`, `is_accurate`, and `record_state` markers already give
the dashboard everything it needs to present live data as unconfirmed.

## Finalization and Reconciliation

Finalization is the transition from a live-owned provisional session to an
archive-owned finalized session. It reuses the implemented archive path rather
than inventing a second writer.

- A session becomes archive-eligible under the existing freshness policy, after
  the recorded archive grace period. Live streaming ending does not by itself
  make a session eligible.
- The existing atomic archive persistence already performs stale-row
  replacement inside one transaction under row locks. Finalization extends that
  replacement to also claim rows currently owned by `live_signalr` in the
  `provisional` state.
- The replacement remains all-or-nothing. A failed finalization leaves the
  provisional session intact and readable; it never leaves a session with some
  finalized and some provisional rows.
- **FastF1 is authoritative at finalization.** Where archive and live disagree
  on a lap time, a deletion, a classification, or an entry identity, the archive
  value wins and the provisional value is replaced, not merged.
- A provisional row whose natural key is absent from the archive snapshot is
  removed by the replacement rather than retained as an orphan. Live-only
  artefacts — a lap the feed reported and FastF1 does not recognize — are not
  evidence, and keeping them would make a finalized session a mixture of two
  sources.
- Reconciliation is observable. The finalizing transaction records how many
  provisional rows were replaced, added, and removed, so a systematic feed
  disagreement is measurable instead of silent.

This preserves the recorded decision that clients never see two archive
snapshots mixed, and extends it: a client never sees provisional and finalized
rows mixed either.

## Failure Behavior

- A collector failure never deletes previously finalized sporting data.
- A live session that never finalizes remains provisional and clearly marked;
  it does not block archive backfill, which reaches it through normal
  eligibility.
- Losing the live feed mid-session is not a session failure. It is a recorded
  gap, and the archive pass is what closes it.
- The collector stops gracefully without taking new sessions, matching the
  existing worker's shutdown contract.

## Open Decisions

These change the migration shape and are not settled by this document:

1. Whether `session_ingestions` becomes `(session_id, source)` as recommended,
   or live ingestion state is tracked in a separate table.
2. Whether provisional rows are readable through the existing session endpoints
   behind an explicit opt-in parameter, or only through a separate live channel.
3. Whether reconciliation differences are retained as durable history for
   measurement, or only logged and counted.
