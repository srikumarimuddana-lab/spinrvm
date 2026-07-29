# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | (this branch) |
| Related issue or gap ID | Follow-up to the report-template storage recommendation delivered this session |

## 1. Issue / gap identified

The real SGI regulator PDF templates (`backend/static/sgi_forms/D00032_driver_details_template.pdf`, `D00033_vehicle_details_template.pdf`) were plain checked-in files with no mechanism to detect if one were ever silently swapped, corrupted, or replaced with an unreviewed version — a wrong template generates a wrong regulator submission, potentially without anyone noticing until SGI rejects a filing.

## 2. Root cause

Not a bug — this is new tooling requested as part of the report-template storage recommendation. Git already provides an audit trail for *intentional, reviewed* changes to these files (a PR diff shows the binary changed), but nothing previously verified at runtime that the deployed file matches what the code was actually tested against.

## 3. Fix / remediation

New `services/data_transfer/sgi_template_versions.py`: a small registry pinning each template's SHA-256 checksum and the revision date printed on the form itself (both templates are stamped "04/2021" by SGI). `startup_verify_sgi_templates()` runs once at process startup (`core/lifespan.py`, right after the Stripe SDK config block) and raises loudly on any mismatch — in production this fails the deploy; in dev/staging it logs and continues, so a local template swap-in-progress doesn't block the server. Does **not** check per-request (would be wasted I/O for a file that only changes via a reviewed commit).

Recommended workflow going forward (documented in the module's own docstring): when SGI issues a new form revision, replace the template file and update its `TemplateVersion` entry in the **same commit**, so code review sees the binary diff and the version-registry diff together.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** New module, no other callers exist yet besides the one `lifespan.py` startup call added here. `sgi_form_filler.py` (the actual template-filling code) is untouched — this is a verification layer alongside it, not a change to how forms are filled.
- The startup check adds negligible latency (two file reads + SHA-256 over ~2.5MB total) to server boot, once.
- **Production behavior change:** a genuinely corrupted or unexpectedly-modified template file will now **fail the deploy** in production, where previously it would have silently generated forms from whatever was on disk. This is an intentional tightening, not a regression — flagging per CLAUDE.md's "no silent behavior change" rule even though the previous behavior (silently generating from an unverified file) was itself the risk being closed.
- Dev/staging: a mismatch only logs an error and continues, so this cannot block local development if someone is mid-way through legitimately updating a template.

## 5. User-experience effect

- **No admin-facing UI change.** This is a backend startup-time integrity check with no UI surface. The only observable effect is a louder failure mode (deploy fails instead of generating a wrong PDF) if a template is ever tampered with or corrupted — internal/ops-facing only.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/data_transfer/sgi_template_versions.py` (new) | Checksum registry + `startup_verify_sgi_templates()` | Core of the fix |
| `backend/core/lifespan.py` | Calls the verification at startup, production-strict / dev-lenient | Wire it in |
| `backend/tests/test_sgi_template_versions.py` (new) | 3 tests: real templates pass, mismatch raises, missing file raises | Coverage |

## 7. Before / after

```python
# Before: no verification — a swapped/corrupted template file would be
# silently used to generate every SGI form until someone visually caught it.

# After (core/lifespan.py, startup):
try:
    from services.data_transfer.sgi_template_versions import startup_verify_sgi_templates
    startup_verify_sgi_templates()
except Exception as e:
    logger.error(f"SGI template checksum verification failed: {e}", exc_info=True)
    if settings.ENV.lower() == "production":
        raise
```

## 8. Rollback plan

Plain `git revert` — no schema, no data, no API contract. Reverting removes the startup check entirely; the templates themselves are untouched either way.

## 9. Verification performed

- [x] `pytest backend/tests/test_sgi_template_versions.py` — 3/3 passed, including confirming the real checked-in templates match their pinned checksums (i.e., the registry is correct as of this commit, not just internally consistent).
- [x] `ruff check` clean on all 3 touched/new files.
- [x] Manually ran `startup_verify_sgi_templates()` standalone against the real files and confirmed it both passes cleanly and raises correctly on an injected mismatch/missing-file case.
- [ ] Not verified against a real production boot sequence in this session (no way to run the full FastAPI app with real settings in this sandbox) — the `lifespan.py` wiring was reasoned about against the adjacent, already-proven Stripe-config block's identical raise/log pattern, not independently executed end-to-end.

## 10. What was NOT verified / deferred

- No automated CI step re-verifies the checksums on every push (only at actual server startup) — a template accidentally committed without updating the registry would be caught the first time the server boots against it (CI's own backend-test job would catch it too, if that job's Postgres setup weren't currently broken — see the unrelated CI infra failure on PR #2853), not at PR-review time. Could add a dedicated CI check for this specifically if desired.
