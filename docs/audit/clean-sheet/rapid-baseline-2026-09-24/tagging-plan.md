# Tagging plan — taxonomy coverage today and rollout (Lane A6)

**Source:** Lane A6 (sonnet / spinr-observability-reviewer), 2026-09-24. Coverage table is in `A6-observability.md` §(a); this file carries the rollout plan. Evidence: VERIFIED where a path:line is given; the T0 list below is PROPOSED (CLAUDE.md does not enumerate T0 components today).

## Coverage snapshot (from A6 §a)

| Key | Present today | Est. T0 coverage |
|---|---|---|
| `domain` | `logger.bind(domain=…)` in 41 loguru modules; ~15 explicit Sentry-tag sites | ~15–20% |
| `surface` | stamped globally by `utils/sentry_scrub.py:150` and all 3 frontend Sentry inits | 100% |
| `component` | not a tag anywhere | ~0% |
| `owner` | `.github/CODEOWNERS` (review routing only) | 0% as telemetry |
| `tier` | not found | 0% |
| `data_class` | not a tag; PII enforced structurally (`utils/pii.py`, `utils/sentry_scrub.py`) | 0% as a key |
| `sla` | implicit via metric names only | 0% explicit |
| `flag` | informal; ACTION_ITEMS R2/R3 track convergence | ~10% |
| `vendor` | module names / `monitoring/synthetic-checks.yaml` only | ~5% |
| `env`/`region` | `environment=` on every Sentry init; region check only in admin `sentry.server.config.ts` | 100% env / region backend+mobile missing |
| `cost_center` | not found | 0% |
| `scale_limit` | `utils/maps_budget.py` only | ~5% |

## Rollout

**Phase 1 — stop the bleeding (new code only).**
1. Land OBS-006's CI check (`test_error_level_loguru_calls_bind_domain` in `backend/tests/test_loguru_call_conventions.py`) with an initial baseline/allowlist of today's ~290 offenders (generate via the same grep used in OBS-001) so it blocks only *new* unbound ERROR-level calls.
2. Extend the same check (or a companion) to require `domain`+`surface` on any *new* `sentry_sdk.capture_exception`/`capture_message` call site (the ~15 explicit-tag sites already do this correctly and can serve as the reference pattern).
3. Document the pattern once in CLAUDE.md's Observability Conventions section (it currently documents *what* tags are required but not the *mechanism* — `logger.bind(domain=...)` before `.error(`, module-level `logger = logger.bind(domain=...)` for single-domain modules like `utils/outbox_worker.py:28`).

**Phase 2 — backfill the 10 T0 (money/safety/dispatch) components, in priority order:**
1. `backend/routes/rides/matching.py` — dispatch engine, 0% bound today, highest SLA stakes (offer→accept <2s)
2. `backend/routes/drivers/ride_flow.py` — accept/reject/complete, stdlib logging, no tag-promotion path at all
3. `backend/routes/drivers/_shared.py` — same as above, 168 combined error sites with ride_flow.py
4. `backend/routes/rides/booking.py` — ride creation, pre-auth, PI-reuse security checks
5. `backend/routes/rides/cancellation.py` — cancellation-fee capture, pre-auth release
6. `backend/routes/rides/safety.py` — SOS/insurance-period-adjacent ride safety path
7. `backend/services/dispatch_service.py` — already has 3 `logger.warning` calls; confirm its `.error(` sites too
8. `backend/services/payment_service.py` — already partially bound (`:124,2475,2536`); close the remaining gaps
9. `backend/utils/refresh_tokens.py` — already tags `domain="auth"` on explicit captures; verify coverage is complete, not just present
10. `backend/routes/webhooks.py` — Stripe webhook path; pair with OBS-002's new duration metric so `domain=payments` events and the SLA metric ship together

**Enforcing CI check:** the OBS-006 test, made baseline-gated → after each T0 file above is backfilled, remove it from the baseline allowlist so it can never regress silently. This is the same "additive over destructive, backfill after new-code gate" shape CLAUDE.md's release gates already mandate elsewhere.

**Phase 3 (Later, PROPOSED — not from the lane):** add `tier` and `data_class` as `COMMENT ON TABLE/COLUMN` on the T0 tables (`rides`, `payments`, `financial_events`, `driver_insurance_periods`, `safety_incidents`, `drivers`) and `owner`/`tier` as a module docstring header enforced by the same static scanner. Never big-bang retag (standards-and-scale.md §4).
