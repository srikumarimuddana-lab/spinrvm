# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Claude (agent session) |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | (this branch's PR) |
| Related issue or gap ID | ACTION_ITEMS.md A40 finding #13/#19 (`docs/audit/2026-08-18-full-fleet-whole-app-audit.md`) |

## 1. Issue / gap identified

`_supersede_and_flag_pending_review` (`backend/documents.py`) swallowed a
failed `driver_documents` supersede write with `logger.warning` and no
exception detail — the same silent-failure class already fixed elsewhere in
this session (N13, `routes/drivers/ride_cancel.py`'s `auth_status=released`
write).

## 2. Root cause

The `try/except Exception` around the supersede `update_one` call logged at
WARNING (not ERROR) and dropped the exception's structured detail — for a
Supabase `DatabaseError`, `str(e)` alone collapses to the generic "Database
operation failed", losing the actually-useful underlying cause. Left this
way, a failed supersede leaves the driver's prior `approved`/`pending` docs
active after a re-upload, with no operationally-visible signal that anything
went wrong.

## 3. Fix / remediation

Mirrors N13's exact pattern: log at ERROR with `exc_info=True`, and extract
`e.details["original"]` when the exception carries Supabase's structured
detail (via `hasattr(e, "details")`). No behavior change beyond the log —
the function still doesn't re-raise (a failed supersede must not block the
rest of document processing), and both call sites are unaffected.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped every caller of
  `_supersede_and_flag_pending_review`: two call sites in `documents.py`
  itself and one in `routes/admin/documents.py` (the admin manual-upload
  flow). All three call it exactly as before — return type and no-raise
  contract are both unchanged, only the log level/detail on the failure
  path changed.
- No schema, no state-machine, no money path touched.
- **Could this regress a currently-working flow?** No — happy-path behavior
  (successful supersede) is byte-for-byte unchanged; only the failure-path
  log output changes.

## 5. User-experience effect

None. Purely an internal observability fix — no driver, rider, or admin
facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/documents.py` | `_supersede_and_flag_pending_review`'s except-branch now logs at `logger.error(..., exc_info=True)` with `e.details["original"]` when available, instead of `logger.warning(f"...: {e}")` | Close the silent-swallow gap per CLAUDE.md's "Do not silently swallow errors" convention, mirroring the already-reviewed N13 fix |
| `backend/tests/test_documents.py` | New regression test asserting `logger.error` (not `.warning`) fires with `exc_info=True` on a failed supersede write | Prove the fix and prevent regression |

## 7. Before / after

```python
# Before
    except Exception as e:
        logger.warning(f"Could not supersede prior docs for driver {driver_id}: {e}")
```

```python
# After
    except Exception as e:
        _original = e.details.get("original") if hasattr(e, "details") and isinstance(e.details, dict) else None
        logger.error(
            "Could not supersede prior docs for driver %s: %s%s",
            driver_id, e, f" — {_original}" if _original else "",
            exc_info=True,
        )
```

## 8. Rollback plan

`git revert` is safe and sufficient — pure logging-level/detail change, no
schema, no data mutation, no external contract change.

## 9. Verification performed

- `pytest tests/test_documents.py -q --no-cov` → 48 passed.
- `ruff check backend/documents.py backend/tests/test_documents.py` → clean.
- Grepped every caller of `_supersede_and_flag_pending_review` (3 total, 2
  internal + 1 in `routes/admin/documents.py`) to confirm isolation.

## 10. What was NOT verified

- No live Supabase check — mocked `update_one` only, per this module's
  existing test convention.
- documents.py logs via loguru, not stdlib `logging` — confirmed pytest's
  `caplog` fixture does not reliably capture loguru output, so the test
  patches `documents.logger` directly instead; not re-verified against a
  real loguru sink/handler configuration.
