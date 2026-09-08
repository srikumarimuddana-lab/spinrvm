# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | ittalenthireca-sketch |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | (filled in on push) |
| Related issue or gap ID | ACTION_ITEMS.md A34 (dual-run cutover — legacy SIN/DOB import), pre-flight fix from a `spinr-security-auditor` gate review |

## 1. Issue / gap identified

`apply_legacy_sin_dob_import` (the never-yet-run backfill that will write real SIN
data for 157 legacy-imported drivers) never checked the result of the
`encrypt_driver_pii` vault RPC before trusting it. If that RPC ever returned a
null/empty payload without raising (e.g. `vault.create_secret()` returning NULL),
the code would write `sin = NULL` to the driver row while still stamping
`legacy_import_metadata.sin_written = true` — a silent "success" that actually
left the SIN unwritten, with no operator-visible error.

Found during a pre-flight `spinr-security-auditor` review requested before this
script's first-ever `--apply` run against production (the script is fully built
and tested but has never been executed against real data — see
`docs/change-log/2026-08-19-legacy-sin-dob-import.md`).

## 2. Root cause

`encrypt_pii()` (line 361) is a thin wrapper: `getattr(res, "data", None)` — it
returns whatever the RPC gives back, including `None`, without raising. The
call site in `apply_legacy_sin_dob_import` assigned that return value directly
to `fields["sin"]` and derived `sin_written` from `bool(plain_sin)` (i.e. "we
had a plaintext SIN to try encrypting") rather than from whether encryption
actually produced ciphertext. The `.is_("sin", "null")` write-guard doesn't
catch this either — writing `sin = NULL` to an already-NULL column still
"succeeds" (matches the guard, returns non-empty `res.data`).

## 3. Fix / remediation

At the SIN write call site in `apply_legacy_sin_dob_import`, validate that
`encrypt_pii()`'s result parses as a UUID (the documented shape
`encrypt_driver_pii` returns per migration 138 — `_secret_id::text`) before
including it in the update or marking `sin_written`. On a malformed/null
result, raise `RuntimeError` naming the `old_driver_id` instead of writing
anything — the row is left completely untouched (idempotent: a later re-run
picks it up normally, per the function's existing idempotency contract).

The shared `encrypt_pii()` helper itself was left unchanged (see blast radius
below) — the validation is scoped to this one call site only.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this one call site.** `encrypt_pii()` is also
  used for `license_number` at two other sites
  (`driver_import_service.py:916` and `:2319`, both in the bulk-import
  vehicle/license-update paths) — grepped for every caller
  (`grep -rn "encrypt_pii("`), confirmed only 3 total call sites, and only
  the SIN one was touched. The shared helper's return contract
  (`str | None`, never raises on a bad RPC result) is unchanged, so the two
  `license_number` call sites behave exactly as before.
- This backfill script has never been run against production — there is no
  live data path currently depending on the old (unvalidated) behavior.
  Nothing regresses for existing drivers.
- No interaction with the ride state machine, background loops, or
  money/wallet deltas — this is a one-shot admin/CLI data-import path only.

## 5. User-experience effect

None. Backend-only, admin/ops-tool-facing (CLI script + one admin HTTP
route), not reachable by any rider or driver flow, and not yet run against
real data.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/driver_import_service.py` | `apply_legacy_sin_dob_import`'s SIN branch now validates `encrypt_pii()`'s result is UUID-shaped before writing it or marking `sin_written` | Prevent a silent encryption failure from being recorded as a successful legacy-SIN import |
| `backend/tests/test_legacy_sin_dob_import_service.py` | Fake RPC now returns a real UUID (`uuid.uuid5` of the plaintext) instead of `"enc::<plaintext>"`; added `test_apply_refuses_to_record_success_when_encryption_returns_no_ciphertext` | Match the real `encrypt_driver_pii` return shape; cover the new failure-mode guard |
| `backend/tests/test_admin_legacy_sin_dob_backfill.py` | Same fake-RPC fix (`"enc::"` → real UUID) for the admin HTTP route's own fixture, which shares the same `apply_legacy_sin_dob_import` code path | The admin commit endpoint hits the identical validation; its fixture needed the same fix or `test_commit_writes_encrypted_sin_and_dob` would fail against the new guard |

## 7. Before / after

```python
# Before
fields: dict[str, Any] = {"updated_at": now_iso}
plain_sin = upd.get("_plain_sin")
if plain_sin:
    fields["sin"] = encrypt_pii(plain_sin)
    fields["sin_last4"] = sin_last4(plain_sin)
    fields["sin_collected_at"] = now_iso
```

```python
# After
fields: dict[str, Any] = {"updated_at": now_iso}
plain_sin = upd.get("_plain_sin")
if plain_sin:
    encrypted_sin = encrypt_pii(plain_sin)
    try:
        uuid.UUID(encrypted_sin)
    except (TypeError, ValueError) as e:
        raise RuntimeError(
            f"encrypt_driver_pii returned no usable ciphertext for "
            f"old_driver_id={upd['old_driver_id']!r} — refusing to write"
        ) from e
    fields["sin"] = encrypted_sin
    fields["sin_last4"] = sin_last4(plain_sin)
    fields["sin_collected_at"] = now_iso
```

## 8. Rollback plan

`git revert` is sufficient here — this fix only tightens a validation on a
write path that has never yet run against real data, so there is no live
state to reconcile. If reverted, the pre-existing (already-tested, already
reviewed) behavior returns; no data migration or flag flip is needed either
way.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_legacy_sin_dob_import_service.py tests/test_driver_import_service_coverage.py tests/test_admin_legacy_sin_dob_backfill.py` — 113 passed. Broader blast-radius sweep: `pytest tests/ -k "driver_import or legacy_sin_dob or driver_bank or legacy_driver"` — 244 passed, 1 skipped (pre-existing skip, unrelated).
- [ ] Manual repro steps followed in staging — not applicable; this session has no staging/production Supabase access (see A34's own note in `ACTION_ITEMS.md`). No manual repro was possible or attempted.
- [x] Blast-radius grep performed: `grep -rn "encrypt_pii(" backend/` — 3 call sites found (this one + 2 `license_number` sites), only this one modified.
- [x] Reviewed against relevant `CLAUDE.md` convention(s): "Do not silently swallow errors" (DB/PII write failures must surface loudly, not fall through to a misleading success state) — this fix is a direct application of that rule.
- [x] `ruff check` and `ruff format --check` clean on all 3 changed files.
- [ ] Feature-flagged — not applicable; this is a one-shot admin/CLI script, not a standing user-facing flow.

**What was NOT verified:** this fix was never exercised against the real `encrypt_driver_pii` Postgres function or a real Supabase vault — only against an in-memory fake RPC in both test files. The actual failure mode this guards against (`vault.create_secret()` returning NULL without raising) has never been observed in practice; this is a defense-in-depth fix for a theoretical gap a security audit identified, not a reproduction of an observed incident.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no live data involved)
- [x] Blast radius is stated, not assumed (grepped all 3 `encrypt_pii()` call sites)
- [x] No silent behavior change to an already-shipped flow — this script has never been run against production; nothing currently live depends on the old behavior

**Separate, unresolved blocker — not addressed by this fix:** this fix makes
the SIN/DOB import script safer to run, but does **not** clear it for
production. `ACTION_ITEMS.md` B11 (legal/consent basis for reusing
predecessor-app-collected SIN data) is still open, per a
`spinr-regulatory-compliance-checker` pre-flight review run alongside this
security review. **`--apply` must not run against production until that
legal determination is recorded.**
