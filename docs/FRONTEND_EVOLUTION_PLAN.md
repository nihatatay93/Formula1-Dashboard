# Frontend evolution plan

Goal: bring the dashboard to the depth and polish of a mature F1 analytics
product, using `app.formula1dashboard.com` as the reference for *what a serious
version of this looks like*.

Each phase is self-contained and can be executed in a fresh session. Every
phase names the files to read first, what to build, how to prove it works, and
what not to do.

---

## The headline finding

**The gap is mostly data and features, not CSS.** The reference app's 18
sections are backed by aggregates this project does not yet expose, not by a
prettier stylesheet. The existing visual system ("Pit Wall" in
`frontend/src/index.css`) is already close in tone — dark graphite, red for
attention, cyan for data, monospace tabular figures.

So roughly **70% of this plan is backend endpoints** over data already in
PostgreSQL, and about 30% is frontend.

The good news: almost nothing needs new ingestion. See the capability map below.

---

## Phase 0 — Discovery (complete; findings recorded here)

Sources consulted:

- `https://app.formula1dashboard.com/` — home, `/race-pace/`, `/head-to-head/`,
  and the full navigation manifest (18 routes, read from the DOM).
- This repository: `backend/app/db/models/{lap,result,entry,driver,session,event}.py`,
  the route table in `backend/app/api/` and `backend/app/live/api.py`,
  `frontend/src/contracts.ts`.

### Reference information architecture

Eighteen sections, in the order the reference presents them:

```
Home · Live Timing · Schedule · Results
Driver Standings · Constructor Standings · Drivers · Teams
Driver Stats · Head To Head · Consistency · Race Pace · Pit Stops
Tech Updates · Used Elements · Destructors Championship · Track DNA
Changelog
```

### Patterns worth taking

Observed directly, not assumed:

1. **A home page made of KPI cards with deltas.** Each headline number carries
   a change against the previous round (`+411,000 (+2.99%)`), which turns a
   figure into a trend.
2. **A countdown to the next session** with round number and event name.
3. **Season progress** as a percentage with a circuit outline.
4. **Persistent scope selectors** as chips in a top bar — Season / Race /
   Drivers / Team — rather than a drill-down. The scope stays as you move
   between analysis pages.
5. **Lap filtering as a first-class control**: a "clean laps" toggle and an
   outlier cutoff slider defaulting to 107%. This is the single most important
   analytical idea on the site — raw lap times are meaningless without it.
6. **All drivers at once**, coloured by team, rather than a small selection.
7. **Distribution, not just series**: race pace shown as box plots per driver
   beside the lap-by-lap evolution.
8. **Highlight modes** (Off / Personal Best / Fastest Lap) instead of
   selection checkboxes.
9. **Export on every chart** (a download control in each card header).
10. **Diverging bars for head-to-head** — one bar per metric split between two
    drivers, showing both the absolute value and the share.
11. **Standings tables with an evolution column** showing movement.

### Capability map — what the existing schema already supports

Confirmed by reading the models. **No new ingestion required:**

| Reference feature | Data already stored |
|---|---|
| Driver & constructor standings | `results.points`, `session_entries.team_name` |
| Standings evolution by round | `results.points` per event, ordered |
| Results browser | `results.*` (position, grid, status, gap) |
| Race pace, all drivers | `laps.lap_time_us`, `position`, per entry |
| Clean-lap filtering | `laps.is_accurate`, `deleted`, `pit_in_time_us`, `pit_out_time_us`, `track_status` |
| Outlier cutoff (107%) | percentile over `laps.lap_time_us` |
| Consistency | standard deviation of clean `laps.lap_time_us` |
| Qualifying head-to-head | `results.grid_position` |
| Race head-to-head | `results.position`, `points`, `status` |
| Wins / podiums / poles | `results.position`, `grid_position` |
| Stint & tyre strategy | `laps.stint_number`, `compound`, `tyre_life_laps`, `fresh_tyre` |
| Pit stop analysis | `laps.pit_in_time_us`, `pit_out_time_us` |
| Sector analysis | `laps.sector_{1,2,3}_time_us` |
| Speed traps | `laps.speed_fl_kph`, `speed_st_kph` |
| Telemetry traces | `lap_telemetry_samples` (already built) |

**Available in FastF1 but not currently ingested** — each needs a new
ingestion path and a migration:

- Weather per session (`session.load(weather=True)`, currently `False`)
- Circuit/corner geometry (`session.get_circuit_info()`) — the basis for a
  "Track DNA" style page
- Race control messages for the archive

**Not available from FastF1 at all.** These are curated editorial content on
the reference site, not derived data. Do not plan to compute them:

- Tech upgrades, used power-unit elements
- Crash damage costs
- Liveries, driver photography, team logos
- The "Stats & Records" news feed

### Allowed APIs

Only these exist today. **Do not invent endpoints; add them deliberately in
the phase that needs them.**

```
GET    /api/v1/seasons/{year}
POST   /api/v1/seasons/{year}/backfill
GET    /api/v1/backfill-jobs/{job_id}
GET    /api/v1/sessions/{session_id}
GET    /api/v1/sessions/{session_id}/results
GET    /api/v1/sessions/{session_id}/entries/{entry_id}/laps
POST   /api/v1/sessions/{session_id}/entries/{entry_id}/laps/{n}/telemetry
GET    /api/v1/sessions/{session_id}/entries/{entry_id}/laps/{n}/telemetry
GET    /api/v1/upstreams/fastf1/usage
GET    /api/v1/auth/session · POST /api/v1/auth/{login,logout}
GET/POST/DELETE /api/v1/live/session · /live/auth · /live/recordings · /live/replay
WS     /api/v1/live/stream
```

### Anti-patterns to guard against

- **Do not copy the reference's visual design, layout, or branding.** Take the
  information architecture and the analytical ideas; render them in the
  existing Pit Wall system. Team logos and driver photography are licensed
  assets and must not be scraped.
- **Do not fetch laps per driver in a loop.** The existing
  `/entries/{id}/laps` endpoint is keyset-paginated per entry; twenty drivers
  would be twenty paginated walks. Phase 4 adds a session-wide endpoint.
- **Do not compute standings in the browser** by fetching every session's
  results. It is an aggregate; it belongs in SQL.
- **Do not add a chart library.** The existing charts are hand-built inline
  SVG (`PaceTrendChart.tsx`, `LapTelemetryChart.tsx`) and match the design
  system. Follow that pattern.
- **Do not put two measures on one y-axis.** Established in
  `LapTelemetryChart.tsx`: facets share an x-axis instead.
- **Do not invent FastF1 fields.** Every earlier telemetry bug in this project
  came from assuming upstream shapes. Verify against a real session first.

---

## Phase 1 — Standings and season aggregates (backend)

**Why first:** standings are the backbone of six reference pages, and nothing
in this project computes them yet.

**Read first**
- `backend/app/api/seasons.py` — route and response-model conventions
- `backend/app/api/contracts.py` — `ApiModel`, error shapes, `DecimalIdentifier`
- `backend/app/db/models/result.py`, `entry.py` — the columns to aggregate
- `docs/HISTORICAL_API_DESIGN.md` — the contract style to match

**Build**
- `GET /api/v1/seasons/{year}/standings/drivers` — position, driver, team,
  points, wins, podiums, poles, DNFs, and a per-round points array
- `GET /api/v1/seasons/{year}/standings/constructors` — the same, grouped by
  team
- Both computed in SQL over `results` joined to `session_entries`, restricted
  to race and sprint sessions, and only over sessions with
  `data_available = true`

**Verify**
- `uv run pytest tests/test_standings.py`
- Points for a completed season match the official championship table for at
  least three drivers — a real external check, not a self-consistent one
- A season with no ingested races returns an empty standing, not an error
- Ordering ties break by wins, then by best finish

**Do not**
- Award points from a table of your own. `results.points` is what FastF1
  reported; use it. Sprint scoring differs by era and is already baked in.
- Include practice or qualifying sessions in points aggregation.

---

## Phase 2 — Home page rebuild

**Why:** the current Home has two cards. It should be the page you leave open.

**Read first**
- `frontend/src/Home.tsx` — what exists
- `frontend/src/shared/MetricCard.tsx`, `StatusPill.tsx`, `ProgressTrack.tsx`
- `frontend/src/index.css` `.home-*` rules

**Build**
- Next-session countdown from `sessions.scheduled_start_at`, with round and
  event name; degrade to "date to be confirmed" for deferred events, which
  `season.deferred_future_events` already reports
- Season progress: rounds completed / total, sessions ingested / discovered
- Championship leaders: top five drivers and top three constructors from
  Phase 1, each linking into the full standing
- Last race result: podium and fastest lap
- Keep the two existing path cards (Archive / Live), which do a job the
  reference has no equivalent of

**Verify**
- Vitest covers: countdown renders, an event without exact timing does not
  render a bogus countdown, an empty season renders without crashing
- Playwright: home renders with the fixture season and routes into the archive
- No horizontal overflow at 375px — the existing e2e already asserts this

**Do not**
- Add "crash damage" or "tech upgrade" style cards. That data does not exist
  here and cannot be derived.
- Fetch every session to build the summary. Extend the season overview
  endpoint if a figure is missing.

---

## Phase 3 — Standings and results views

**Read first**
- `frontend/src/archive/SeasonCalendar.tsx` — the list/table idiom
- `frontend/src/archive/ResultsTable.tsx` — existing result rendering
- `frontend/src/shared/StatusPill.tsx`

**Build**
- Driver standings view: table with position, driver, team colour bar, points,
  wins, and a compact per-round sparkline from the Phase 1 array
- Constructor standings view: the same shape
- Results browser: every race of a season with podium and fastest lap,
  expanding into the full classification

**Verify**
- Vitest: an in-progress season and a completed one both render
- Row order matches the API order exactly — never re-sort client-side, or the
  tie-breaks silently change
- Keyboard navigable; team colour is never the only carrier of identity

**Do not** re-implement points logic in TypeScript.

---

## Phase 4 — Race pace analysis (complete)

**The most valuable phase.** This is what makes the reference feel like an
analysis tool rather than a results site.

**Built as specified, with three findings worth carrying forward:**

- `laps.track_status` is not one code. FastF1 concatenates every status seen
  during the lap, so `"1"` is green throughout but `"12"`, `"21"` and `"671"`
  are not. Matching on "contains a 1" would admit most yellow laps.
- 107% is a qualifying benchmark and is too tight for race pace. At Monaco it
  excludes 53% of clean laps -- the winner's own median clean lap is 104.3% of
  their best. `beyond_cutoff` is therefore a flag, never a filter, and the
  slider reaches 130%.
- Team colours cannot be re-picked: several real pairs fail CVD separation
  (Williams `#1868DB` and Red Bull `#4781D7` sit at deltaE 8.0 for normal
  vision, below the floor of 15; Cadillac and Haas are both achromatic), and
  team-mates share one colour exactly. Identity therefore never rests on
  colour -- every distribution row is named, team-mates are dashed, and the
  evolution chart is focus-and-context with the raised line labelled.

**Read first**
- `frontend/src/archive/PaceTrendChart.tsx` — the existing SVG chart idiom
- `frontend/src/archive/lapAnalysis.ts` — existing selection statistics
- `frontend/src/archive/LapTelemetryChart.tsx` — faceting, hover, the
  colour-and-contrast rules that were validated
- `backend/app/api/telemetry_data.py` — keyset pagination style
- The `dataviz` skill before writing any chart code

**Build — backend**
- `GET /api/v1/sessions/{id}/laps` — every entry's laps for one session in one
  response, with query parameters `clean_only` and `outlier_cutoff` (a
  percentage of the session best, default 107)
- "Clean" is defined server-side and documented: not deleted, `is_accurate`,
  no pit in or out on the lap, and green track status. Put that definition in
  one place so the chart and any future export agree.

**Build — frontend**
- Lap-time evolution: one line per driver, team-coloured, all drivers at once
- Distribution: a box plot per driver, ordered by median
- Controls: clean-laps toggle, outlier cutoff slider, highlight mode
  (off / personal best / session best)
- Stint shading behind the evolution chart from `stint_number`

**Verify**
- Backend: a session with 20 drivers returns in one request; clean filtering
  removes known pit laps in the fixture; the cutoff excludes a lap 8% off
- Frontend: 20 series render; toggling clean laps changes the point count;
  the box plot's median matches `lapAnalysis` for one driver
- Measure the payload for a real 70-lap race. If it is large, paginate by
  driver group rather than sending less data per driver.

**Do not**
- Colour by finishing position. Colour follows the entity — a filter that
  changes the driver set must not repaint the survivors.
- Draw a box plot without stating the whisker rule in the caption.
- Loop the per-entry laps endpoint.

---

## Phase 5 — Head to head and consistency (complete)

**Read first**
- `frontend/src/archive/lapAnalysis.ts` — `compareLapSelections` already does
  a two-way comparison
- Phase 1's standings endpoints

**Built, with two corrections to this plan:**

- The qualifying record cannot come from `grid_position`. That column is
  populated only on race and sprint results and is NULL on every qualifying row
  in the archive; the capability map above was wrong. It reads the qualifying
  session's own `position` instead, which is also the better measure -- a grid
  slot reflects penalties as much as pace.
- The race record cannot compare raw `position` either. A retirement and even a
  "Did not start" still carry one, because it orders the cars rather than
  ranking them. Comparing them scored a race a driver never started as a loss,
  so the record counts only races both drivers were classified in and reports
  the rest as excluded.

**Build — backend**
- `GET /api/v1/seasons/{year}/head-to-head?driver_a=&driver_b=` — qualifying
  record from the qualifying session's `position`, race record from `position`
  among classified finishers, plus points, wins, podiums, poles, DNFs, and best
  finish
- `GET /api/v1/seasons/{year}/consistency` — per driver: median, standard
  deviation and interquartile range of clean laps, and finish-rate. Measured
  over race sessions only and normalised to each session's best clean lap: an
  absolute season median mixes circuits, and practice laps reach 221% of a
  session best in this archive, which would swamp any spread metric.

**Build — frontend**
- Diverging bars per metric, both absolute values and shares, driver identity
  carried by label as well as colour
- Team-mate quick-select, since that is the comparison people actually want
- Consistency as a ranked table plus a spread chart

**Verify**
- A pair who never raced together returns zeroes, not an error
- Sessions where one driver did not start are excluded from the qualifying
  record, and the exclusion is stated in the response
- Standard deviation matches a hand-computed value for one driver in the
  fixture

**Do not** compare drivers across different seasons in one call; regulation
changes make it meaningless. Scope to a season.

---

## Phase 6 — Strategy and stints (complete)

**Read first**
- `backend/app/db/models/lap.py` — `stint_number`, `compound`,
  `tyre_life_laps`, `fresh_tyre`
- `frontend/src/index.css` `.live-tyre`, `.compound` — existing compound
  colours

**Built.** One finding worth carrying forward: subtracting the two pit
instants does not always give a pit-lane time. A car that pits under a red
flag sits there through the suspension, and in the 2026 Monaco race those
sixteen entries computed to between 2023 and 2158 seconds against 19 to 66 for
the seventy that were not — a clean separation with no overlap. A red flag in
the lap's `track_status` (the code is `5`, anywhere in the concatenated
string) is the discriminator, so those stops are listed without a duration
rather than with an absurd one.

Served from the existing `/sessions/{id}/laps` response, which gained
`pit_in_time_us`, `pit_out_time_us` and `track_status`, rather than a second
endpoint: strategy and pace describe the same laps.

**Build**
- A strategy chart per race: one horizontal bar per driver, segmented by
  stint, coloured by compound, with pit stops marked
- Pit-stop table from `pit_in_time_us` / `pit_out_time_us`, with the caveat
  that this is time in the pit lane, not stationary time — the reference's
  "1.99s" figure is stationary time and is **not** derivable here

**Verify**
- Stint boundaries agree with `stint_number` changes
- Compound colours match the existing tyre palette
- The pit-lane-versus-stationary caveat appears in the UI, not only in code

**Do not** present pit-lane time as "pit stop time". It is roughly 20 seconds
longer and would look wrong to anyone who knows the sport.

---

## Phase 7 — Design and interaction polish

**Read first**
- `frontend/src/index.css` — the whole token block at the top
- The `dataviz` skill
- `docs/PROJECT_CONTEXT.md` → "Dashboard visual system"

**Build**
- A persistent scope bar (season / event / session / drivers) shared by the
  analysis views, replacing repeated drill-down
- Consistent empty, loading and error states for every view — currently
  uneven
- A React error boundary at the shell, so one render error does not blank the
  page (still outstanding from the production audit)
- Chart export as PNG or CSV from each card header
- Density toggle for tables

**Verify**
- Every new view has all three states covered by a test
- Axe or equivalent finds no contrast failures
- Keyboard traversal of the scope bar works
- `npm run build && npm test && npm run test:e2e`

**Do not** introduce a second styling approach. Extend the token block.

---

## Phase 8 — Verification

1. `cd backend && uv run ruff check app tests && uv run pytest`
2. `cd frontend && npx tsc --noEmit && npm run build && npm test -- --run && npm run test:e2e`
3. Grep for the anti-patterns: any new `fetch(` outside `api.ts`; any chart
   library in `package.json`; any second y-axis; any hard-coded points table
4. Run the full stack and walk each new view against a real ingested season
5. Confirm every number shown either comes from the API or is computed in a
   tested pure function — no arithmetic inline in JSX
6. Update `docs/PROJECT_CONTEXT.md` with a decision entry per phase

---

## Sequencing note

Phases 1–3 are worth doing as one run: they share the standings work and
together change the product's character from "ingestion tool" to "dashboard".
Phase 4 is the single highest-value phase and can follow independently.
Phases 5–6 are additive. Phase 7 should come last, since it polishes surfaces
the earlier phases create.

This programme is larger than everything built so far. It is worth landing
backups and CI first — an outstanding item from the production audit — because
this plan multiplies the surface area that CI would be protecting.
