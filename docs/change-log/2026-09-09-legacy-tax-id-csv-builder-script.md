# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-09 |
| Author | Claude Code (session) |
| Surface(s) | backend (tooling only — no runtime/API surface) |
| Domain (Sentry tag) | admin |
| PR / commit link | (this change) |
| Related issue or gap ID | User-reported: no file in the raw Mongo export matches `backend/routes/admin/tax_id_import.py`'s required `phone,sin,gst_bn` CSV shape |

## 1. Issue / gap identified

The "Legacy Tax-ID (SIN + GST/HST BN) Backfill" admin tool requires an upload with the exact header `phone,sin,gst_bn`. No file in the raw MongoDB export (`Mongo.zip`) is shaped like that or named anything like "Tax-ID" — the real source data (`banks.csv`) is keyed by `driver_id` (a Mongo ObjectId), not phone, and there was no existing script to produce the tool's required shape.

## 2. Root cause

`banks.csv` carries every legacy driver's SIN and GST/HST business number, but only becomes phone-addressable by joining `banks.csv.driver_id` → `drivers.csv._id` → `drivers.csv.phone` (the same join `services/driver_import_service.join_legacy_bank_sin_dob` already performs, production-verified, for the sibling SIN/DOB backfill). That function returns phone + SIN but was scoped to SIN/DOB only — nothing in the repo also pulled `banks.csv`'s `gst` column and emitted the 3-column CSV this specific tool needs.

## 3. Fix / remediation

Added `backend/scripts/build_legacy_tax_id_csv.py`: a local, offline CLI tool that reads `banks.csv` + `drivers.csv` (via the existing, tested `read_mongo_export_csv`/`join_legacy_bank_sin_dob` from `driver_import_service.py` — no join logic duplicated), pulls each row's `gst` value, normalizes it exactly the way `tax_id_import.py`'s own `_build_plan` does (`.replace(" ", "").upper()`), and writes a ready-to-upload CSV with the exact required header. Rows with no phone match, or with neither SIN nor GST, are excluded and counted (not silently dropped — the run prints how many).

**PII handling (PIPEDA):** the script prints only aggregate counts, never a row's phone/SIN/GST value. Format-invalid SIN/GST values are still written to the output (advisory count only) rather than silently dropped or "corrected," since the tool's own `validate_sin`/GST regex is the single source of truth for whether a row is actually rejected — this script does not re-implement or override that validation. The generated output CSV is written outside the git repository (this session wrote it into the operator's own upload directory, not any repo path) and is real driver PII — it must be deleted locally once uploaded, never committed, and never pasted into a chat/log. This session ran the script once against a real, user-supplied `banks.csv`/`drivers.csv` pair to hand back the ready-to-upload CSV; that run's output file was not committed and this repo change contains no PII, only the reusable logic.

## 4. Risk & impact on existing functionality

- **Blast radius: none at runtime.** This is an offline CLI script with no route, no scheduler entry, no caller anywhere else in the codebase. It makes zero Supabase/API calls — pure local file-to-file transform. Grepped: no other module imports `build_legacy_tax_id_csv`.
- Reuses (does not modify) `driver_import_service.join_legacy_bank_sin_dob`/`read_mongo_export_csv`, `routes/drivers/payouts._GST_BN_RE`, and `utils/sin.validate_sin` — all pre-existing, already-tested functions. No existing function's behavior changed.
- The output CSV still passes through `tax_id_import.py`'s own `/validate` (dry-run, no writes) before `/commit` — this script does not bypass that gate, it only produces the file that gate consumes.

## 5. User-experience effect

Internal-tooling only; no rider/driver/corporate-admin-facing change. The internal admin (super_admin) operating this migration step gets a script that produces the correct upload shape instead of hand-editing a CSV.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/scripts/build_legacy_tax_id_csv.py` | New script: banks.csv + drivers.csv → phone,sin,gst_bn CSV | Close the gap between the raw Mongo export and this admin tool's required input shape |
| `backend/tests/test_build_legacy_tax_id_csv.py` | New unit tests (synthetic data only — a Luhn-valid but fake SIN, a fake GST BN): happy path, unmatched phone, neither-value exclusion, SIN-only, GST-only, malformed-value-still-written, and a PIPEDA assertion that summary stats never carry a string value | Lock in the join/filter logic; guard against a future edit accidentally leaking a value into the printed summary |

## 7. Before / after

```python
# Before: no script existed to produce this shape at all.
```

```python
# After
out_rows.append({"phone": phone, "sin": sin_raw, "gst_bn": gst_norm})
```

## 8. Rollback plan

`git-revert-safe` — new, uncalled files only; nothing else references them.

## 9. Verification performed

- [x] `pytest backend/tests/test_build_legacy_tax_id_csv.py` — 7/7 pass.
- [x] `ruff check` / `ruff format --check` — clean.
- [x] Ran once against the real, user-supplied `banks.csv` (164 data rows) + `drivers.csv`: 0 rows failed the phone join, 0 rows had neither SIN nor GST, 164 rows written — output header confirmed byte-for-byte `phone,sin,gst_bn`.
- [x] Confirmed via `git status` that no PII file (input or generated output) touched the repository working tree — only the two new code files.

## What was NOT verified

- Not confirmed that the tool's own `/validate` step accepts the generated CSV end-to-end (no live Supabase/admin-session access from this sandboxed session) — the header shape and phone-join logic are verified directly; whether every individual SIN/GST value passes the tool's own format validation is for the operator's own Preview run to confirm (the script's printed "format-invalid" counts are a heads-up, not a guarantee).

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed (no callers, no runtime path)
- [x] No silent behavior change to an already-shipped flow (net-new script only)
