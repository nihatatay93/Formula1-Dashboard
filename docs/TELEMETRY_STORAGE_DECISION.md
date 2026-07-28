# Historical Telemetry Measurement and Storage Decision

## Status

- Date: 2026-07-28
- Decision status: accepted
- First implementation: standard PostgreSQL with lap-scoped, on-demand
  telemetry
- TimescaleDB: deferred with explicit review triggers

## Purpose

This document records the evidence collected before adding historical
high-frequency telemetry. It decides the first storage strategy for Milestone 4
without implying that every historical lap should be ingested.

The measurement deliberately used the project’s pinned FastF1 3.8.3 runtime,
persistent cache, serialized FastF1 boundary, and PostgreSQL request ledger.
It did not bypass upstream request controls or expose raw upstream data.

## Reproducible Measurement

The command accepts one or more exact lap identities:

```bash
docker compose run --rm --no-deps worker \
  /opt/venv/bin/python -m scripts.measure_fastf1_telemetry \
  --sample 2018:1:FP2:HAM:14 \
  --sample 2018:1:Q:HAM:19 \
  --sample 2018:1:R:VET:20
```

Each sample was checked against the existing normalized archive before
measurement:

- 2018 Australian Grand Prix Practice 2, Lewis Hamilton, lap 14
- 2018 Australian Grand Prix Qualifying, Lewis Hamilton, lap 19
- 2018 Australian Grand Prix Race, Sebastian Vettel, lap 20

All three lap summaries had a stored time and no pit-in or pit-out transition.
FastF1 session resources were processed sequentially.

The measurement utility records:

- sample count;
- observed lap-relative duration;
- median and p95 sample interval;
- observed samples per second;
- pandas in-memory bytes;
- availability of selected car and position channels;
- a conservative PostgreSQL planning estimate.

The 160-byte PostgreSQL planning estimate includes a normalized sample row and
its primary lap/sample access path. It is not a measured table size. Milestone 4
must measure the real migrated table after sample persistence.

## Results

| Session | Driver/lap | Samples | Duration | Median interval | P95 interval | Samples/sec | Frame memory | PostgreSQL estimate | Position X/Y/Z |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Practice 2 | HAM 14 | 368 | 88.721 s | 240 ms | 280 ms | 4.137 | 49,076 B | 58,880 B | unavailable |
| Qualifying | HAM 19 | 617 | 81.164 s | 137 ms | 240 ms | 7.590 | 171,546 B | 98,720 B | available |
| Race | VET 20 | 371 | 88.042 s | 240 ms | 280 ms | 4.203 | 49,475 B | 59,360 B | unavailable |

Aggregate:

- 3 representative laps
- 1,356 total samples
- 371 median samples per lap
- 452 average samples per lap
- 270,097 bytes of pandas frame memory
- 216,960 bytes of estimated normalized PostgreSQL storage

Speed, RPM, gear, throttle, brake, DRS, distance, and relative distance were
present for every sample. Historical position data was unavailable for the
measured Practice 2 and Race laps. The Qualifying lap had merged position data,
which also increased the observed sample count and frequency.

## Scale Projection

The completed local 2018 archive contains 58,002 driver laps across 105
sessions. Eagerly storing every lap at the observed 452-sample average would
produce approximately:

- 26,216,904 telemetry samples;
- 4,194,704,640 bytes (about 3.91 GiB) using the planning estimate.

This excludes PostgreSQL page fill effects, secondary indexes, WAL, backups,
cache duplication, and future live telemetry. It is sufficient evidence not to
attach telemetry to the existing season backfill or season/session overview
responses.

One explicitly requested average measured lap is approximately 72 KiB by the
same planning estimate. Lap-scoped, on-demand ingestion therefore keeps the
initial local data set proportional to actual analysis use.

## Decision

Use standard PostgreSQL for the first bounded historical telemetry slice.

Milestone 4 will:

1. Queue telemetry only for an explicitly requested stored lap.
2. Persist normalized samples under that lap.
3. Keep sample position fields nullable.
4. Associate a completed telemetry snapshot with the sporting snapshot that
   supplied the lap identity.
5. Serve samples through lap-scoped, keyset-paginated REST reads.
6. Keep telemetry out of season overview, session detail, results, and lap
   summary responses.
7. Use the existing persistent FastF1 cache, shared request ledger, and
   single-concurrency worker rather than introducing Redis.

This access pattern is a narrow primary-key/index scan for one lap, which
PostgreSQL handles without a time-series extension. The first implementation
also avoids making TimescaleDB a mandatory local dependency before continuous
live timing exists.

## TimescaleDB Review Triggers

Re-evaluate TimescaleDB or native PostgreSQL partitioning when any of these
conditions becomes true:

- stored telemetry exceeds 50 million samples or 10 GiB;
- routine product queries span many sessions or more than 100 laps rather than
  one explicitly selected lap;
- continuous live telemetry introduces sustained append volume and retention
  or compression requirements;
- operational measurements show the lap/sample index, vacuum, backup, or
  retention cost is no longer acceptable;
- time-bucket aggregation becomes a backend product requirement.

Crossing a trigger requires new measurements and an Alembic migration design.
It does not automatically authorize enabling TimescaleDB.

## Limitations

- Three samples are enough to establish order of magnitude and historical
  nullability, but they do not represent every circuit, season, or FastF1
  backend variation.
- FastF1 position availability varies by session. Car telemetry must remain
  independently usable.
- The storage estimate is intentionally conservative and must be replaced with
  actual PostgreSQL relation/index measurements after Milestone 4.
- Raw FastF1 cache size is separate from normalized database size.
