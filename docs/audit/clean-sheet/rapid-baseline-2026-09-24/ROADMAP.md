# Roadmap — Now / Next / Later (rapid baseline 2026-09-24)

Fix-time targets from one-hour-run.md §4: CRITICAL < 24 h, HIGH < 1 week, MEDIUM next sprint. "Owner" is a role, not a person. Every item names its flag or config switch and a rollback that does not rely on `git revert` for anything already applied to live data. Items in the same row of "Parallel lane" touch different files and can run at the same time without merge conflicts.

## Now (this week) — HIGH items and zero-code wins

| # | Item | Finding | Owner role | Flag / switch | Rollback | Verify by | Parallel lane |
|---|---|---|---|---|---|---|---|
| N1 | Set `SPINR_PROCESS_ROLE=api` on one Fly burst machine, then app machines; one `worker` process; mirror on Railway | A3-001 | Backend/SRE | env var (no code) | unset env → role `all` | `flyctl logs` shows role; DB loop-poll query counts drop | L-infra |
| N2 | Refresh the required-status-checks list; re-enable `claude-review.yml` path-filtered to money/auth/dispatch/migrations/safety; lint gate jobs for `continue-on-error` without a CR id | A1 decay (C7/C9/C73/C12) | Repo owner (GitHub admin) | GitHub settings + workflow `paths:` | remove entries / unset secret | a test PR to `backend/routes/payments/` gets a review comment; merge blocked until checks finish | L-ci |
| N3 | Fix the two raw-float tip sums; regression test with 1.10/2.20/3.30; add a semgrep pattern for unwrapped `sum()`; drop the earnings.py exclusion; make pre-commit step 6 block, scoped to SR-03's 12 files | MONEY-001/004/005 | Payments | none (display-only) | revert (no persisted data) | new test passes; SR-03 fails on a scratch branch that reintroduces the pattern | L-money |
| N4 | Land `test_error_level_loguru_calls_bind_domain` with a baseline of today's ~290 offenders; backfill `matching.py`, `drivers/ride_flow.py`, `drivers/_shared.py` | OBS-001/006 (R4) | Observability | test-only | delete the test / shrink baseline | test fails on a new unbound `.error(` in a scratch branch | L-obs |
| N5 | Emit `spinr_drivers_location_write_duration_ms`, `spinr_auth_token_refresh_duration_ms`, `spinr_payments_webhook_duration_ms{event_type}` and `spinr_maps_budget_spent_ratio`; set `ALERT_WEBHOOK_URL` | OBS-002, A3-004 | SRE | additive metrics; env for alerts | remove lines / unset env | series visible on `/metrics`; watchdog alert fires in staging with budget=$0.10 | L-obs (different files from N4: `location.py`, `auth.py`, `webhooks.py`, `maps_budget.py`) |
| N6 | Set `OTP_PEPPER` explicitly in production | SEC-A2-001 step 0 | Auth | env var | unset (only in-flight OTPs affected) | OTP login works after restart | L-infra |
| N7 | Inventory every `pgsodium.*` reference; run Supabase `list_extensions` on prod (human); rewrite `docs/runbooks/pii-key-rotation.md` to Vault-only primitives | SEC-A2-002 | Security | docs/runbook only | keep old runbook until round-trip test passes | encrypt/decrypt RPC round-trip test on a branch | L-sec |
| N8 | Send the PST question (E1) and the four other tax rows to the accountant with a deadline; record the citation in G9 | MONEY-003, ESCALATIONS A | Founder → accountant | n/a | n/a | G9 closed with URL + date | — (no code) |
| N9 | Shadow-compute the `ride_offers`-based declined-driver set and log divergence from the Redis skip set | DISPATCH-002 step 1 | Dispatch | `app_settings.dispatch_durable_offer_skip` (off) | remove shadow code | divergence log empty over 48 h | L-dispatch |
| N10 | Write ADR-017 recording the no-pinning decision; update threat-model RS-4 to match | SEC-A2-004 | Security | docs | n/a | ADR merged | L-sec (docs only) |
| N11 | Correct stale docs surfaced this run: CLAUDE.md loop count (42→44 + watchdog), "admin JWT fully trusted" wording, money pre-commit claim, `capacity-scaling.md` §2/§4 fleet numbers, `railway.json` `UVICORN_WORKERS` 4→2 | A3-005, SEC-A2-008, MONEY-004 | Platform | config (one JSON value) | revert JSON | `standby-parity-monitor.yml` extended to diff env | L-docs |

## Next (next sprint) — MEDIUM items and the staged security moves

| # | Item | Finding | Owner role | Flag / switch | Rollback |
|---|---|---|---|---|---|
| X1 | Verifiers accept HS256 and ES256 (`JWT_ACCEPT_ASYMMETRIC`); then mint ES256 (`JWT_ASYMMETRIC_MINT`); after 30 days stop accepting HS256; bump `admin_staff.token_version` on any module/role change | SEC-A2-001/008 | Auth | two flags | flip flags back; dual-accept means no forced logout |
| X2 | Admin revocation fails closed on Redis error | SEC-A2-003 | Auth | `ADMIN_REVOCATION_FAIL_CLOSED` | flag off |
| X3 | Flip `dispatch_durable_offer_skip` on (union of DB and Redis sets); product decides DISPATCH-001 and either adds the rider event or corrects `domain-dispatch.md` | DISPATCH-002/001 | Dispatch + product | `app_settings.dispatch_durable_offer_skip` | flag off → Redis-only |
| X4 | Additive `payouts.amount_decimal NUMERIC(12,2)` with dual-write + backfill + dual-read | RECURRENCE-001 (B28) | Payments | `app_settings.payouts_amount_decimal_read` | flag off → FLOAT column authoritative; reconcile query lists mismatches; never drop the FLOAT column during live testing |
| X5 | Measure Redis ops per ping (`INFO commandstats`) and negotiated WS extensions; then Lua-coalesced ping path in shadow mode | A3-002, A3-007 | Realtime | `app_settings.ws_location_lua_path_enabled` | flag off |
| X6 | Backfill the remaining 7 T0 files in `tagging-plan.md`; document the `.bind` mechanism in CLAUDE.md | OBS-001 | Observability | none | n/a |
| X7 | Provision the Grafana Cloud agent and the two day-one alert rules (CR-2026-008) | OBS-002 | Human with dashboard access | vendor config | disable rule |
| X8 | Receipt `tax_breakdown` always populated (close the write-path gap or compute the split from booking-time rates); check `email_receipt.py` and `corporate_statement_pdf.py` siblings | MONEY-002 | Payments | none (display) | revert |
| X9 | Optimistic concurrency on `PUT /admin/drivers/{id}` via optional `expected_updated_at` → 409; sweep other admin editors | CONCURRENCY-001 | Admin | optional field (backward-compatible) | ignore the field |
| X10 | Fork-parity: single `issue_otp_code()` helper + literal-count test; promote pre-commit check 11 to CI failure for registry pairs; delete `rider-app/utils/apiClient.ts` | SEC-A2-007, RECURRENCE-002, RELIABILITY-002 | Platform | none | revert (no live data) |
| X11 | Starlette `GZipMiddleware` dark behind `ENABLE_GZIP`; stream the 10k-row exports | A3-003 | Backend | env | env off |
| X12 | Fill the vendor plan-of-record table (Supabase tier, Redis plan, Sentry quota, Vercel/EAS) in `capacity-scaling.md` §5 and the renewal calendar | A3-006 | Human with dashboard access | docs | n/a |
| X13 | Full 44-loop replay-safety read; PIPEDA retention + insurance-period pass | DEFERRED §3 (A4, A8) | Realtime + compliance reviewers | report only | n/a |

## Later (after live testing stabilises, or Rewrite-only)

| # | Item | Finding | Why later |
|---|---|---|---|
| L1 | App-layer envelope encryption (AES-256-GCM, ca-central KMS) beside the Vault column, dual-read | SEC-A2-002 | Reassess after N7 — Vault-only may suffice if Supabase's "interface unchanged" statement holds |
| L2 | Play Integrity / App Attest as a risk signal (never a hard block) on go_online and settlement | SEC-A2-005 | Needs App Check enforcement confirmed first (C3, console-only) |
| L3 | Per-replica pub/sub channel sharding | A3-002 step 3 | Only after X5 measurements |
| L4 | Device signal at signup (referral/promo farming) | SAFETY-004 | Needs a privacy impact assessment and product decision (ESCALATIONS E10) |
| L5 | Passkeys/WebAuthn for super_admin; DPoP-bound refresh tokens | A2 research table | After X1 lands |
| L6 | `tier`/`data_class` as `COMMENT ON TABLE/COLUMN` for T0 tables; `owner` in module headers | tagging-plan.md Phase 3 | Never big-bang; after X6 |
| L7 | Locust 1× / 10× / 3× soak / chaos runs against staging | capacity-table.md | Needs X12's real limits first, otherwise the tripwires are guesses |

## Three items that can start today in parallel without touching the same files
1. **N1 + N6** (env/config on Fly and Railway; no repo files beyond `railway.json`/`fly.toml`).
2. **N3** (`backend/routes/drivers/earnings.py`, `.semgrep/spinr-rules.yml`, `.claude/hooks/pre-commit`, one new test).
3. **N4** (`backend/tests/test_loguru_call_conventions.py` + a baseline file, then `backend/routes/rides/matching.py` and `backend/routes/drivers/{ride_flow,_shared}.py`). N5 is also disjoint from all three (`location.py`, `auth.py`, `webhooks.py`, `maps_budget.py`).
