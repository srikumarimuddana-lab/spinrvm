# Change Impact & Risk Log — addendum

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | Claude Code (session) |
| Surface(s) | backend (documentation only — no code change) |
| Domain (Sentry tag) | rides |
| Related issue or gap ID | ACTION_ITEMS.md A30, Finding 0 follow-up |

## What this is

A30's audit (2026-08-13) confirmed via live Supabase query that the
legacy booking importer (`docs/change-log/2026-07-29-legacy-booking-import.md`)
had actually run against production — that original doc's own "Not run
against live or staging Supabase" verification note (§10) was stale by
the time it mattered, and nothing ever recorded when/how the real commit
happened. This is that missing record.

## When and how it happened

Confirmed via the `audit_logs` table itself (read-only query against the
real project, `soavhtdhefowwvforzwb`) — the single source of truth for
this event, since no change-log entry or commit message documents it
directly:

```
action:            legacy_booking_import
entity_type:        rides
actor_id:            admin-001 (super_admin)
created_at:          2026-07-29 18:48:11 UTC
imported_rides:      224
offset_payouts:      60
drivers_recounted:   60
sum_offset_payouts:  $2,179.66
```

This matches the original change-log's documented CSV scope (224 of
1,210 exported bookings) and the "offsetting `legacy_import` payout row
per matched driver" design described in §3 of that doc — 60 distinct
drivers matched, not 64 as that doc's root-cause section estimated from
an earlier, coarser export-level count ($2,207.06 vs. the real
$2,179.66 offset), which is expected: the final commit only creates a
payout row per *matched* driver, and the root-cause note was describing
the theoretical exposure across the whole export before the
phone-match/skip logic ran.

It ran via the admin-dashboard Bulk Operations → Legacy Booking Import
path (the "intended path" per the original doc's §3), same day the
importer merged (2026-07-29), by a super_admin actor.

## Why this matters enough to record

Both the Saskatchewan Transportation Act's 7-year trip-record retention
and this repo's PIPEDA data-provenance conventions treat "when was this
data actually committed to the system of record" as a fact worth being
able to answer without reconstructing it from an audit-log query — this
note makes that fact discoverable from `docs/change-log/` directly,
matching the precedent the later GST backfill set for itself
(`docs/change-log/2026-08-16-gst-backfill-executed.md`).

## Verification

- [x] Read-only `audit_logs` query against the real production project,
  cross-checked against the original importer's own documented scope
  (224 rides) and payout-offset design — both match exactly
- No code changed; this is a documentation-only addendum
