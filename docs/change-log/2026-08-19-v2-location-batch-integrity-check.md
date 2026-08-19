# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Claude (agent session) |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | (this branch's PR) |
| Related issue or gap ID | ACTION_ITEMS.md A40 finding #7 (`docs/audit/2026-08-18-full-fleet-whole-app-audit.md`) |

## 1. Issue / gap identified

`POST /drivers/location-batch`'s v2 (active-trip outbox) code path never ran
the GPS spoofing/teleport integrity check that the legacy v1 path has always
run before trusting a point for the driver's live map marker.

## 2. Root cause

`_persist_v2_location_batch` (`backend/routes/drivers/location.py`) was added
alongside the v1 handler but never wired in `check_location_integrity()`
(`backend/utils/location_integrity.py`) — the same helper the v1 branch
calls just above it in the same file. `persist_trip_location_batch`
(`backend/utils/breadcrumbs.py`), which v2 calls to write the historical
breadcrumb rows, stores the client-supplied `mocked` flag as data for the
settlement-time anomaly filter (`utils/trip_distance.py`) but never rejects
on it — that filter only protects *billed distance*, not the driver's
real-time `drivers.lat`/`drivers.lng` marker, which dispatch, the rider map,
and admin all read as the driver's live position.

## 3. Fix / remediation

Before `_persist_v2_location_batch` writes the batch's latest point to
`drivers.lat`/`lng`, it now calls the same `check_location_integrity()` used
by v1 (mock flag, impossible speed, low/zero accuracy, teleport-via-Redis).
If untrusted, the live marker write is skipped and a warning is logged with
the rejection reason — the already-persisted breadcrumb rows and batch ack
are untouched (regulatory GPS-trace retention and the settlement anomaly
filter still see every point exactly as before).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped the whole backend: `_persist_v2_location_batch`
  has exactly one caller, `update_location_batch` (the route handler itself),
  which is the only production entry point for this code.
- **What else reads/writes `drivers.lat/lng`:** the v1 branch in the same
  file (unchanged — already ran this check), `routes/drivers/location.py`'s
  `/nearby` read path, dispatch's geo-bound driver fetch
  (`services/dispatch_service.py`), and admin driver-list views. None of
  those write the column; this change only makes the v2 write path as
  strict as the v1 write path already is, so no other writer is affected.
- **Could this regress a currently-working flow?** Only for a genuinely
  spoofed/mocked/teleporting point: that point's coordinates no longer move
  the driver's live marker (same outcome v1 already produces). A legitimate
  point is unaffected — `check_location_integrity` only flags mock-flagged,
  physically-impossible-speed, zero/very-low-accuracy, or >10km-in-<10s
  points.
- **Background loops:** none of the 37 startup loops touch this path.
- **Money:** none — settlement-time distance calculation
  (`utils/trip_distance.py`) is untouched; it already runs its own,
  independent anomaly filter over the stored breadcrumbs regardless of this
  change.

## 5. User-experience effect

- **Driver:** none observable — a legitimate driver's location behaves
  identically. Only a spoofed/mocked GPS source stops moving the driver's
  visible marker on the rider map / dispatch / admin view, which is the
  intended effect, not a regression.
- **Rider / admin:** no live-session-visible change under normal operation.
- Not gated behind a feature flag — this closes a real-time integrity gap
  the v1 path has always had; there is no meaningful "ship dark" option for
  a security/fraud guard on the same code path it's supposed to protect.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/location.py` | `_persist_v2_location_batch` now calls `check_location_integrity()` before writing the live `drivers.lat/lng` marker; skips the marker write (not the whole batch) on failure | Close the v2 spoofing-check gap without touching breadcrumb/regulatory persistence |
| `backend/tests/test_location_batch.py` | New regression test asserting the marker write is skipped (not the breadcrumb persist) when the integrity check rejects | Prove the new guard fires and that the graceful-degradation contract holds |

## 7. Before / after

```python
# Before
if latest is not None:
    lat = latest.latitude if latest.latitude is not None else latest.lat
    lng = latest.longitude if latest.longitude is not None else latest.lng
    if lat is not None and lng is not None:
        update_data = {"lat": lat, "lng": lng, "updated_at": datetime.now(timezone.utc)}
        if latest.heading is not None:
            update_data["heading"] = latest.heading % 360
        await db_supabase.update_one("drivers", {"id": driver["id"]}, update_data)
```

```python
# After
if latest is not None:
    lat = latest.latitude if latest.latitude is not None else latest.lat
    lng = latest.longitude if latest.longitude is not None else latest.lng
    if lat is not None and lng is not None:
        trusted, reason = await check_location_integrity(
            driver["id"], lat, lng,
            speed=latest.speed, accuracy=latest.accuracy, mocked=latest.mocked,
        )
        if not trusted:
            logger.warning("location-batch v2: rejected live marker update ...")
        else:
            update_data = {"lat": lat, "lng": lng, "updated_at": datetime.now(timezone.utc)}
            if latest.heading is not None:
                update_data["heading"] = latest.heading % 360
            await db_supabase.update_one("drivers", {"id": driver["id"]}, update_data)
```

## 8. Rollback plan

`git revert` is safe and sufficient — this is a pure code-path addition with
no schema change, no data migration, and no live-data mutation of its own
(it only gates an existing write). Reverting restores the prior
unconditional marker write.

## 9. Verification performed

- `pytest tests/test_location_batch.py tests/test_location_batch_revoked_session.py -q --no-cov` → 20 passed.
- `ruff check backend/routes/drivers/location.py backend/tests/test_location_batch.py` → clean.
- Grepped every caller of `_persist_v2_location_batch` and `check_location_integrity` to confirm isolation and that the reused helper's contract (return `(trusted, reason)`, never raises) matches what v1 already assumes.
- Read `utils/breadcrumbs.py`'s `persist_trip_location_batch` and `utils/trip_distance.py` in full to confirm the settlement-time/regulatory paths are independent of this change and unaffected.

## 10. What was NOT verified

- No live Supabase / staging check — mocked `get_rows`/`update_one` only, per this repo's existing unit-test convention for this module.
- No live Redis check of the teleport-detection cache path (`check_location_integrity`'s `redis_get`/`redis_set` calls) — relies on the function's own existing test coverage in `location_integrity.py`'s test suite, not re-verified here.
- No end-to-end verification against a real spoofed device/emulator; reasoned from the existing v1 code path's identical, already-shipped behavior.
