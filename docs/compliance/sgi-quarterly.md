# SGI Provincial Reporting — Data Inventory & Gap Analysis

> **Status:** Gap write-up — the periodic (quarterly/annual) reports are still
> _not_ implemented pipelines; the on-demand trip-record export now is (see
> **2026-08-01 update** below).
> **Created:** 2026-06-02 · **Owner:** unassigned · **Review with:** legal +
> founder before building the two still-open reports.
>
> This is the file `.claude/context/regulatory-sk.md:96` refers to as
> "_template in `docs/compliance/sgi-quarterly.md` — to be created_". It does
> **two** things: (1) documents what trip-distance and insurance-period data
> we already capture and how long it survives, and (2) records the open
> questions that block us from actually producing an SGI submission. See
> [Gaps](#4-gaps-what-is-not-built) and
> [Open questions](#5-open-questions-blocking-implementation).
>
> **2026-08-01 update:** `scripts/compliance_export.py` is now built — but
> only for the on-demand trip-record obligation (§1 row 3), which had an
> already-confirmed SLA (≤14 days, <30 min) and didn't depend on §5's open
> questions about SGI's *periodic-submission* channel/format. It applies the
> strictest PII redaction available (driver_id/ride_id only, no rider
> identity, no raw address/coordinates — see §6) and writes one
> `compliance_export_events` audit row per run. The quarterly ride-volume and
> annual driver-roster reports are unchanged: still blocked on §5.

---

## 0. The question this answers

> _"Per SGI rules we're supposed to record distance travelled. Are we doing it,
> and can we extract it and submit to SGI?"_

**Recording: yes** — and at finer granularity than a single per-trip total.
**Submitting: no** — the data lives in the DB, but there is no export or
submission tooling, and the exact format SGI expects is not yet confirmed.

### Note on "every kilometre"

SGI does **not** require a literal per-kilometre odometer ledger. The actual
obligations (per `.claude/context/regulatory-sk.md`) are trip-level and
period-level records, retained for fixed windows, plus periodic aggregate
submissions. What matters for the **SGI Auto Fund TNC commercial layer** is
that distance can be **attributed to the correct insurance period** (1 / 2 / 3)
— which we do capture (see §2). Keep that framing; don't build a per-km meter.

---

## 1. What we owe SGI / the province

Source: `.claude/context/regulatory-sk.md:94-98` ("Provincial reporting").

| Report | Cadence | Contents | Tooling status |
|---|---|---|---|
| Ride volume + incident count | **Quarterly** | Aggregate counts to SGI | ❌ none |
| Driver roster (license + insurance status) | **Annual** | Per-driver eligibility snapshot | ⚠️ form-fill only: `backend/services/data_transfer/sgi_form_filler.py` + `sgi_field_maps.py` can fill SGI's D00032/D00033 AcroForm PDFs from `drivers` data, but no scheduled/on-demand job drives them yet |
| Trip-record production | **On-demand** (≤14 days of request; target run <30 min) | Per-trip records incl. distance, period linkage | ✅ built — `scripts/compliance_export.py` |

Related retained-for-audit data (not a periodic *submission*, but must be
producible on request):

| Data | Retention | Source of truth |
|---|---|---|
| Insurance-period transitions | 7 years | `driver_insurance_periods` |
| Trip record (incl. distance, fare, times) | 7 years | `rides` |
| Pickup/dropoff GPS | 3 years, then anonymized | `rides` lat/lng + polylines |

---

## 2. What we capture today (data inventory)

### 2.1 Distance, written on ride completion

The completion handler in `backend/routes/drivers.py` (the `update_fields`
block at trip end) writes these to the `rides` row:

| Column | Meaning | Defined in |
|---|---|---|
| `planned_distance_km` | Booking-time haversine estimate | `backend/migrations/15_ride_aggregate_columns.sql:7` |
| `actual_distance_km` | GPS-measured distance of the **billable** (`in_progress`) leg | `backend/migrations/15_ride_aggregate_columns.sql:10` |
| `pickup_to_driver_km` | Distance driver drove to reach pickup | `backend/migrations/15_ride_aggregate_columns.sql:13` |
| `phase_distances` (JSONB) | **Distance split by driver phase** — keys: `navigating_to_pickup`, `arrived_at_pickup`, `trip_in_progress`, `online_idle` | `backend/migrations/15_ride_aggregate_columns.sql:16` |
| `phase_durations` (JSONB) | Time spent per phase | `backend/migrations/39_rides_phase_polylines_durations.sql:20` |
| `phase_polylines` (JSONB) | Downsampled GPS path per phase (~150 pts/phase) | `backend/migrations/39_rides_phase_polylines_durations.sql:23` |
| `route_polyline` (JSONB) | Legacy combined polyline (~200 pts) | `backend/migrations/15_ride_aggregate_columns.sql:19` |
| `gps_points_count` | Breadcrumb count used for the computation | `backend/migrations/15_ride_aggregate_columns.sql` |
| `ride_metrics` (JSONB) | Estimated vs actual distance + duration, per phase and totals | `backend/migrations/89_rides_ride_metrics.sql` |

**How distance is derived:** summed haversine over consecutive GPS pings from
`driver_location_history`, filtered for plausibility (reject segments
implying >150 km/h — SK max 110 + buffer — or >5 km jumps or >5 min gaps),
with an optional road-snap recompute and a fallback to the planned estimate
when GPS is too sparse. A daily rollup function
`compute_driver_phase_distances` exists in
`backend/migrations/54_gps_daily_rollup_fn.sql`.

### 2.2 Insurance periods (the SGI-relevant linkage)

`driver_insurance_periods` (`backend/migrations/64_driver_insurance_periods.sql`,
backfilled by `65_…`) is an **append-only** log of every period transition,
written by `record_period_transition()` in
`backend/utils/insurance_periods.py`. Each row: `{driver_id, period (0–3),
started_at, ended_at, ride_id}`.

There is **no distance column on this table** — distance lives on `rides` and
is joined in via `ride_id`. The phase→period mapping is:

| Insurance period | Driver phase (`tracking_phase`) | Ride status |
|---|---|---|
| 1 (available) | `online_idle` | no assigned ride |
| 2 (en route) | `navigating_to_pickup` / `arrived_at_pickup` | `driver_assigned` / `accepted` / `arrived` |
| 3 (passenger aboard) | `trip_in_progress` | `in_progress` |

➡️ **Per-period distance is therefore derivable** by joining
`driver_insurance_periods` → `rides` and reading the matching `phase_distances`
key. This is the join an SGI export would be built on.

---

## 3. Retention — what survives, and for how long

Source: `backend/migrations/50_pii_retention_purge.sql` +
`docs/runbooks/data-retention.md`. Purge runs daily at 03:00 UTC, leader-locked.

| Data | Window | Action |
|---|---|---|
| `rides` GPS coords + `route_polyline` + `phase_polylines` + `route_snapshot_url` | 3 years | **Anonymized** (NULL/empty), `gps_anonymized_at` stamped — Step A, lines 151–161 |
| `rides` row (full) | 7 years | Hard DELETE — Step B |
| `driver_location_history` (raw breadcrumbs) | 90 days | Hard DELETE — Step C |
| `driver_insurance_periods` | 7 years | Retained; excluded from PII purge |
| `audit_logs` | 7 years | Hard DELETE |

**Critical for SGI:** the 3-year anonymization (Step A) clears only the
**coordinate shapes**. It does **not** touch the distance *scalars* —
`actual_distance_km`, `planned_distance_km`, `pickup_to_driver_km`,
`phase_distances`, `phase_durations`, `ride_metrics` all remain on the row for
the **full 7-year** trip-record window. So the distance numbers a quarterly or
on-demand SGI report needs outlive the privacy scrub by four years, by design.

Raw per-second breadcrumbs (`driver_location_history`) are gone after 90 days —
acceptable, because SGI's GPS interest is pickup/dropoff (3 yr), not the full
trace, and the per-phase distances are already rolled up onto the ride.

---

## 4. Gaps — what is NOT built

| Expected artifact | Referenced at | Status |
|---|---|---|
| `scripts/compliance_export.py` (on-demand trip production, <30 min) | `.claude/context/regulatory-sk.md:98` | ✅ Built 2026-08-01 — see §6 |
| Quarterly ride-volume + incident-count job/report | `.claude/context/regulatory-sk.md:96` | ❌ No job, no aggregation, no submission record |
| Annual driver-roster export (license + insurance status) | `.claude/context/regulatory-sk.md:97` | ⚠️ PDF form-fill exists (`sgi_form_filler.py`); no job schedules or triggers it |
| SGI submission format / template | this file | ❌ Not defined for quarterly/annual (see §5); on-demand doesn't need one — it's handed over per the specific request, not through a standing SGI channel |

The closest existing capability, `GET /admin/export/rides`
(`backend/routes/admin/rides.py`), is **not fit for SGI** because it:

- returns **no distance fields** (`id, pickup_address, dropoff_address, fare,
  status, created_at, rider_name, driver_name` only);
- has **no insurance-period linkage**, no driver/vehicle-at-trip-time linkage;
- is **capped at 1,000 rows** and JSON-only (no batched full-history export);
- emits **raw pickup/dropoff addresses**, which the compliance-export PII rule
  forbids (`.claude/context/regulatory-sk.md` "Common pitfalls": city /
  postal-prefix only unless a subpoena specifies).

---

## 5. Open questions (blocking implementation)

These must be answered — by SGI directly and/or legal — before an export is
built. We have the **data**; we do not have the **spec**.

1. **Submission format & channel.** Does SGI want a portal upload, secure
   email, SFTP, or an API? What file format (CSV / fixed-width / XML / their
   own form)?
2. **Quarterly aggregate — exact fields.** "Ride volume + incident count" at
   what grain? Total rides per quarter? Per service area? Per insurance period?
   Completed only, or attempts? What is SGI's definition of a reportable
   "incident", and does it map to our `safety_incidents` / SOS records?
3. **Distance reporting — is it even required in the periodic report,** or only
   on-demand for a specific trip/claim? If periodic: total fleet km, per-driver
   km, or per-insurance-period km (Period 2 vs 3)?
4. **Annual roster fields.** Which eligibility attributes (license class,
   abstract status, CRC/VSC date, SGI endorsement, inspection date) and at what
   as-of date?
5. **PII boundary for each report.** Confirm what may leave our systems:
   driver_id vs name, area vs address, hashed vs raw rider linkage.
6. **Delivery SLA & retention of the submission itself.** Do we need to retain
   proof-of-submission (and for how long) for audit?

---

## 6. Export shape — implemented for on-demand, illustrative for periodic

The on-demand trip-record export (`scripts/compliance_export.py`) implements
this join, via a PostgREST embedded select rather than raw SQL (`driver_insurance_periods`
selected with an embedded `rides(...)`), scoped by `--start`/`--end` and
optionally `--driver-id`/`--ride-id` instead of a fixed quarter window:

```sql
-- Per-trip, with per-period distance — the join the on-demand export is built on.
-- PII boundary: driver_id/ride_id only, no rider identity, no raw address/coordinates.
SELECT
    dip.period,                              -- 2 / 3 only (ride-linked periods)
    dip.driver_id,
    dip.ride_id,
    dip.started_at, dip.ended_at,
    r.actual_distance_km,                    -- billable leg
    r.phase_distances,                       -- per-phase km
    r.total_fare,
    r.created_at
FROM driver_insurance_periods dip
LEFT JOIN rides r ON r.id = dip.ride_id
WHERE dip.started_at >= :start
  AND dip.started_at <  :end
  AND dip.period IN (2, 3);                  -- commercial-coverage legs
```

It's replay-safe (read-only scan; only write is one `compliance_export_events`
row per invocation — not `audit_logs`, since migration 263 already built the
purpose-fit, RLS-gated, append-only table for exactly this "who exported
what range" evidence), paginates in 1,000-row pages, and outputs CSV or JSON
to a file or stdout. See the script's module docstring for full usage and for
why it deliberately has no raw-address/PII override flag.

The **quarterly aggregate** version below (fleet/service-area rollup, not
per-trip rows) is still illustrative only — §5's open questions about SGI's
periodic submission format/channel remain unanswered, and this sketch should
not be treated as a committed schema until they are:

```sql
-- Illustrative quarterly rollup — NOT implemented. Do not build until §5 is answered.
SELECT
    dip.period,
    COUNT(DISTINCT dip.ride_id) AS trip_count,
    SUM(r.actual_distance_km) AS total_distance_km
FROM driver_insurance_periods dip
LEFT JOIN rides r ON r.id = dip.ride_id
WHERE dip.started_at >= :quarter_start
  AND dip.started_at <  :quarter_end
  AND dip.period IN (2, 3)
GROUP BY dip.period;
```

---

## 7. Definition of done

- [ ] §5 (periodic-report questions) answered and signed off by legal + founder.
- [x] `scripts/compliance_export.py` implemented, with PII redaction — on-demand trip-record production only (2026-08-01).
- [ ] Quarterly aggregate report defined and either scheduled or runnable on demand.
- [ ] Annual driver-roster export defined and wired to a scheduled/on-demand job (form-fill mechanics already exist — see §4).
- [ ] Proof-of-submission retention decided for the periodic reports and wired to `audit_logs` (the on-demand export's own audit trail is done — `compliance_export_events`, 7-year retention per migration 263).
- [ ] This file updated from "gap write-up" to "operational runbook" once the two remaining periodic reports ship.
