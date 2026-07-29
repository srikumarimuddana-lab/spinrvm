# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin, payments |
| PR / commit link | (this branch) |
| Related issue or gap ID | T4A/CRA proposal requested this session — user explicitly chose "keep SIN out of Spinr's systems" + "third-party filer" + "build the SIN-free export now" |

## 1. Issue / gap identified

Spinr already generates a driver-facing T4A-style PDF (`utils/t4a_pdf.py`) but has no way for an **admin** to pull the bulk, per-driver data an actual third-party tax filer needs to file T4A (Box 048) and/or the newer Reportable Platform Operator return (Income Tax Act Part XX.1, effective Jan 2024, which specifically covers "Personal Services" facilitated via a digital platform — squarely covers rideshare). Building an in-house SIN collection + CRA XML e-filing pipeline was explicitly ruled out by the user (SIN must stay out of Spinr's systems); the gap that remains is a clean, accurate, SIN-free data handoff to whichever third-party filer Spinr designates.

## 2. Root cause

Not a bug — new capability. Two things made this tractable to build safely and quickly:
1. Spinr already never stores SIN — migration 92 established that Stripe Connect Express holds it, and `routes/admin/drivers.py`'s existing `POST /drivers/{id}/reveal-sin` endpoint already retrieves it live, one driver at a time, super-admin-gated, fully audited, never persisted. This export follows the identical philosophy for the *rest* of the filer's data (legal name, address): live-fetch from Stripe, never store.
2. `services/stripe_kyc_sync.py` already had the exact Stripe `Account.retrieve` pattern to mirror (`reveal_sin_from_stripe`), just needed a sibling function that deliberately does NOT expand `individual.id_number`.

## 3. Fix / remediation

1. **`services/stripe_kyc_sync.py`**: new `get_legal_name_and_address_from_stripe(driver)` — live Stripe `Account.retrieve()` (no `id_number` expand), returns legal name + mailing address or `None`. Kept as a separate function from `reveal_sin_from_stripe`, not a shared "get everything" helper, specifically so no future caller can accidentally pull the SIN into a bulk path by using the wrong function.
2. **`routes/admin/compliance.py`**: new `GET /api/admin/compliance/t4a-filer-handoff?year=YYYY&format=...` — aggregates a year's completed-ride earnings per driver in ONE bulk query (avoids an N+1 per-driver rides query), filters to the $500 CRA threshold, then live-fetches each qualifying driver's Stripe-verified legal name/address. Restricted to `super_admin` (stricter than the other Compliance reports — this one surfaces verified legal name + address in bulk). Wired through the existing dual-approval export gate, audit logging, and metrics, same as every other report in this module. Registered in `report_branding.REPORT_FORMAT_REGISTRY`.
3. **`admin-dashboard`**: new `downloadT4aFilerHandoff()` API client function; new "T4A Filer Handoff" tab on the Compliance page, visible only to `role === "super_admin"` client-side (backend independently re-checks). Download-only — no email option, so bulk legal-name/address PII never transits email even to an internal address.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** New route, new service function, new UI tab — grepped for other callers of `get_legal_name_and_address_from_stripe`: none exist yet besides this route. `reveal_sin_from_stripe` and the existing reveal-sin admin endpoint are completely untouched.
- **No SIN exposure path introduced.** Explicitly tested (`test_t4a_filer_handoff_never_includes_sin`) that the export's response body never contains anything but a `sin_on_file_at_stripe: Yes/No` flag — never the SIN value itself, never even reached via a code path that could expand it (the Stripe helper function used doesn't request `individual.id_number` at all).
- **Live Stripe API calls at export time**, one per qualifying driver. Explicitly NOT a request-latency-sensitive hot path (CLAUDE.md's SLA table doesn't cover this) — it's an admin-triggered, once-a-year batch operation over a bounded driver count (Spinr's total driver count is in the low hundreds per earlier B14 context). A single driver's Stripe call failing degrades that one row to blank address fields (`get_legal_name_and_address_from_stripe` returns `None` → empty strings), not a failed export.
- No ride, dispatch, payment-processing, or corporate-billing logic touched — this reads existing `rides`/`drivers` data and existing Stripe Connect accounts, writes nothing.

## 5. User-experience effect

- **Internal admin (super_admin only), once-a-year usage pattern.** No rider/driver/corporate-admin-facing change. A driver whose Stripe KYC is incomplete (no address on file) simply shows blank address fields on this export — the admin/filer would need to follow up with that driver directly, which is a real operational note, not a code gap.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/stripe_kyc_sync.py` | New `get_legal_name_and_address_from_stripe()` | SIN-free Stripe fetch |
| `backend/routes/admin/compliance.py` | New `_t4a_filer_handoff_rows()` + `GET /t4a-filer-handoff` endpoint | Core of the export |
| `backend/utils/report_branding.py` | Registered `t4a_filer_handoff` in `REPORT_FORMAT_REGISTRY` | Consistency with other report types |
| `backend/tests/test_compliance_reports_http.py` | 5 new tests (auth, super-admin gate, no-SIN assertion, threshold filter, 503) | Coverage |
| `backend/tests/test_stripe_kyc_sync.py` (new) | 5 tests for the new Stripe helper | Coverage |
| `admin-dashboard/src/lib/api.ts` | New `downloadT4aFilerHandoff()` | Frontend client |
| `admin-dashboard/src/app/dashboard/compliance/page.tsx` | New super-admin-only "T4A Filer Handoff" tab | UI |

## 7. Before / after

```python
# Before: no way to pull bulk per-driver T4A-threshold data for a filer —
# only the driver's own self-service single-year PDF existed.

# After (routes/admin/compliance.py):
@api_router.get("/t4a-filer-handoff")
async def get_t4a_filer_handoff(request, year, format="xlsx", email_to=None, admin=Depends(get_admin_user)):
    if (admin.get("role") or "").lower() != "super_admin":
        raise HTTPException(status_code=403, ...)
    rows, truncated, qualifying_count = await _t4a_filer_handoff_rows(year)
    # ... same render/deliver pipeline as every other Compliance report
```

## 8. Rollback plan

Plain `git revert` — no schema change, no data written, no flag involved. The new endpoint and UI tab simply cease to exist; nothing else depends on them.

## 9. Verification performed

- [x] `pytest backend/tests/test_compliance_reports_http.py backend/tests/test_stripe_kyc_sync.py` — 32 + 5 = 37 tests, all passing, including the explicit no-SIN-in-response assertion and the $500-threshold-filter assertion (confirms a driver's rides are aggregated correctly and an under-threshold driver is never even queried against Stripe).
- [x] Full related suite (`test_compliance_reports_http.py`, `test_stripe_kyc_sync.py`, `test_report_branding.py`, `test_sgi_form_filler.py`, `test_sgi_template_versions.py`) — 83/83 passing.
- [x] `ruff check` clean on every touched/new backend file.
- [x] Real production build (`npm run build`) for `admin-dashboard` — succeeded, `/dashboard/compliance` compiles with the new tab.
- [ ] Not tested against a real Stripe Connect account or real production ride data in this session — the Stripe interaction is mocked in tests; the actual per-row Stripe latency/failure behavior at scale (dozens of qualifying drivers) has not been observed live.

## 10. What was NOT verified / deferred

- **The actual filing determination (T4A vs Part XX.1 vs both, and whether one exempts the other) is explicitly NOT made by this code** — that's a tax-advisor decision the export's own subtitle text says as much ("filing decision is a determination for the chosen filer/tax advisor, not made by this export"). This tool produces data, not a filing recommendation.
- No automated test exercises the live Stripe API — appropriately so (this repo's convention is to mock Supabase/external services in unit tests), but it does mean the first real run against production Stripe accounts is the actual end-to-end verification.
- Whether Part XX.1 genuinely applies to Spinr's specific business model, and the exact de minimis threshold/verification requirements under it, was described based on published CRA guidance in this session's research, not confirmed by a tax advisor — flagged explicitly to the user before building anything (see conversation).
