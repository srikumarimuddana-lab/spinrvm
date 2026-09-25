# API Inventory — every FastAPI route (Tier B matrix, part 1/2)

**Lane:** ad hoc Tier-B matrix build (this session) · **Written:** 2026-09-25 · **Report-only.** Evidence labels per master convention: VERIFIED / INFERRED / ASSUMED / UNKNOWN.

Builds on `docs/audit/clean-sheet/W0-SUMMARY.md` (§9: "693 HTTP endpoints" from the Cartographer's inventory pass — this file re-derives the count independently by parsing the route decorators directly, rather than citing that figure) and `02-findings/security.md` SEC-R10-011/012 (cited in §5/§6 below rather than re-derived).

## §0 Method (reproducible)

Two scripts, run against `backend/` at HEAD (no server boot — everything is static analysis):

1. **Mount resolution** reuses `backend/scripts/check_route_shadowing.py`'s own AST helpers
   (`_parse_imports`, `_parse_include_router_calls`, `_resolve_module_file`, `_router_own_prefix`,
   `_routes_of`) — this is an existing, real tool in the repo (not written for this audit) whose
   `--server-mounts` mode already resolves every router `server.py` mounts directly. Its own
   documented limitation is that it skips routers imported from a **package** (a directory with
   its own `__init__.py`: `routes.rides`, `routes.drivers`, `routes.admin`) rather than a single
   module. This session added a package walker for those three (reusing `_routes_of`/`_router_own_prefix`
   the same way the tool's own `registration_order()` does for a CLI-target package, with one bug
   fix: the tool's regex for the `for _sub in (...)` submodule tuple in `drivers/__init__.py` chokes
   on an inline `#` comment between two entries — this session's copy strips comments before
   splitting) plus two dual-import-pattern routers the plain AST alias walk cannot see because they
   are imported inside a `try/except ImportError` block per CLAUDE.md's dual-import convention
   (`disputes_admin_router` from `routes/disputes.py`, imported at `routes/admin/__init__.py:70/72`)
   or imported directly by `server.py` from a package submodule outside the package's own
   aggregation (`admin_auth_router` from `routes/admin/auth.py`).
2. **Auth-dependency resolution** captures three independent sources, all AST-based, since a route's
   real auth gate can live at any of them and often isn't on the route decorator itself:
   - `Depends(...)` calls in the route handler's own function signature (regex over the sliced
     signature text — VERIFIED where a known name matched, INFERRED-complete for anything the
     name allowlist missed, since the raw `Depends(...)` capture below is a superset check).
   - `dependencies=[...]` on the **router's own construction** (`X_router = APIRouter(...,
     dependencies=[Depends(get_admin_user)])`) — this is invisible at the individual route and was
     the source of several false "no auth" positives in an earlier pass of this same script (e.g.
     every route in `backend/documents.py`'s `admin_documents_router`, `features.py`'s
     `admin_support_router`/`pricing_router` — all gated at construction, not per-route).
   - `dependencies=[...]` on the `PARENT.include_router(ROUTER, ...)` call in `server.py` (the
     `require_module(...)` module-gate pattern — e.g. `corporate_accounts_router`,
     `corporate_wallet_router`) and, for the admin package specifically, at
     `admin_router.include_router(X_router, dependencies=[Depends(require_module("Y"))])` inside
     `routes/admin/__init__.py` (the *real* per-admin-subrouter RBAC gate — CLAUDE.md and
     `docs/audit/clean-sheet/02-findings/security.md` §1.1 already document that this file's own
     comments call this "audit \[03-1\]").
3. **Rate-limit decorator** presence: any decorator between the route decorator and the `def` line
   whose text contains "limit" (case-insensitive) — catches both the named per-route limiters
   (`ride_read_limit`, `cancel_ride_limit`, `api_rate_limit`, ...) and any `@limiter.limit(...)`-style
   call. Absence is reported as `—`; per the method rules on absence claims this was checked with
   the single spelling "limit" substring-matched against every decorator line found, not an exact
   name list, so it should catch stylistic variants — but a rate limit enforced entirely inside the
   function body (not as a decorator) would not be seen. None were found on manual spot-check of a
   sample of `—` rows, but this is not exhaustively verified for all ~070 such rows.
4. **Money-moving** and **idempotency-key** columns are **keyword heuristics only, INFERRED, not a
   real classification** — money: the route's source file path or the first ~80 body lines match
   `stripe|wallet|payout|charge|refund|ledger|fare|payment|surcharge|surge|corporate_wallet|allowance|
   topup|invoice|billing` (case-insensitive); idempotency: the literal substring "idempoten" appears
   in the signature or first ~80 body lines (catches `claim_stripe_event`-style comments and
   `Idempotency-Key` header handling, but **not** an idempotency guarantee implemented via a DB
   unique constraint or atomic UPDATE...WHERE with no comment saying so — see CLAUDE.md's "Race
   condition guard for ride acceptance" pattern, which this heuristic would mark idempotency=No even
   though the `{'status': 'searching'}` filter *is* the real idempotency mechanism). Treat both
   columns as "worth a second look", not as ground truth.

Commands (from `backend/`): the two scripts are `check_route_shadowing.py` (existing, unmodified) plus
this session's `extract_routes.py` (per-route decorator/signature/body regex classifier) and
`build_inventory.py` + `merge_and_render.py` (mount resolution + join + render), kept in this
session's scratchpad, not committed to the repo (report-only per this task's brief).

## §1 Totals

- **722 unique route *definitions*** (one row per source-file function that carries an HTTP method decorator, deduplicated by (source file, function name, method) — this is the number comparable to "how many distinct endpoints does this backend define").
- **1,193 total *mount-point registrations*** across every `include_router()` call site — higher than the definition count because several routers are **deliberately double- or triple-mounted** (documented in `server.py`'s own comments): `settings_router`/`legal_documents_router` at root and `/api/v1` for back-compat, `admin_router` at both `/api/admin/...` and `/api/v1/admin/...` (the browser dashboard needs the App-Check-exempt `/api/admin` path; mobile-facing admin tooling historically used `/api/v1`), `corporate_company_router`/`corporate_rider_router` at root, `/api` and (rider variant) `/api/v1`, `files_router` at `/api` and `/api/v1`. This dual-mounting is itself worth noting for a clean-sheet rebuild (§7) — it is a real, load-bearing back-compat mechanism today, not an accident, but it means naive route-counting tools disagree with each other depending on whether they count mount points or definitions; this file reports both.
- **W0-SUMMARY.md's own "693 HTTP endpoints" figure** (from the separate Cartographer inventory pass, `01-inventory/traceability.csv`) sits between this file's two numbers (722 definitions, 1,193 mount-point registrations) — consistent with a different counting method (likely nearer a live OpenAPI-schema route count, which would collapse duplicate `operationId`s some FastAPI versions warn about); not reconciled line-by-line here, flagged as a cross-check for whoever reruns this.

Method breakdown (of the 722 unique definitions): **GET** 331, **POST** 289, **PUT** 48, **DELETE** 40, **PATCH** 14.

Money-moving (keyword heuristic): **253** of 722. Idempotency-keyword hit: **41** of 722. Rate-limit decorator present: **150** of 722.

## §2 Routes with no auth dependency found (any of the three sources)

**45 of 722** route definitions have no `Depends(...)` on the function signature, no `dependencies=` on the router's own construction, and no `dependencies=` on the `include_router()` call that mounts them. Read individually (VERIFIED code read, this session) rather than trusted as a flat list — the large majority are legitimately public by design:

| Category | Routes | Assessment |
|---|---|---|
| Admin auth itself (`routes/admin/auth.py::admin_auth_router`) | login, logout, logout-all, refresh, break-glass, change-password, MFA challenge/confirm/enroll/disable/status, session | **Legitimate** — these mint the token; gating them behind the token they mint is circular. Each does its own inline verification (password/TOTP/break-glass-token check in the function body, not a `Depends`). Cross-ref `security.md` SEC-R10-001/006/007 for the *quality* of that inline verification (env break-glass, MFA rollout) — this file only confirms the route-level shape. |
| Rider/driver auth itself (`routes/auth.py`) | send-otp, verify-otp, firebase, refresh, reactivate, send/verify-email-otp | **Legitimate**, same reasoning. Cross-ref `security.md` SEC-R10-009 (OTP throttle) and SEC-R10-016 (4 divergent OTP implementations) for depth on these specific handlers — not re-derived here. |
| Webhooks (`routes/webhooks.py`) | `POST /webhooks/stripe`, `/webhooks/ses`, `/webhooks/twilio-inbound` | **Legitimate by design** — verified by vendor signature inside the handler (Stripe signature header, SES SNS signature, Twilio request signature), not a bearer token; this is the correct pattern for a vendor-initiated webhook and matches CLAUDE.md's Stripe-idempotency convention. Not independently re-verified this session that every one of the three actually checks its signature before trusting the body (that is `money-cra.md`/`security.md` territory) — flagged, not re-derived. |
| Public read-only content (`routes/settings.py`, `routes/legal_documents.py`, `routes/service_areas.py`, `routes/fares.py` `/fares` `/vehicle-types`, `routes/branding.py` logo, `routes/marketing.py` unsubscribe, `features.py`'s `support_router` `/faqs` `/area-config` `/rides/airport-fee` `/rides/fare-estimate`, `/api/v1/company-info`) | ~15 routes | **Consistent with CLAUDE.md's "public settings" pattern** (Settings-in-DB section) and the documented double-mount reasoning in `server.py`. Fare/vehicle-type/airport-fee reads exposing pricing logic pre-auth is a deliberate booking-flow requirement (rider must see a price before logging in), not an oversight. |
| Share-link / token-in-path (`GET /api/v1/rides/track/{share_token}`, `GET /trip/live/{token}`) | 2 routes | **Likely legitimate** — the path segment itself is the credential (a long random share token), the same pattern as most ride-share "share your trip" links industry-wide. Not independently verified this session that the token is high-entropy / rate-limited against guessing (`rides/sharing.py` token generation not re-read here) — worth one grep by whoever owns this file next. |
| Stripe Connect redirect pages (`routes/drivers/payouts.py`: `/stripe-embedded`, `/stripe-refresh`, `/stripe-return`; `routes/drivers/subscriptions.py`: `/subscription/checkout-return`) | 4 routes | **Verified legitimate on inline read** — `stripe_embedded`'s own docstring: "Public HTML host for the embedded onboarding WebView. Contains no secret (publishable key only); all privileged work goes through the authenticated `/stripe-account-session` endpoint the page calls back into." `stripe_return`/`stripe_refresh` are the analogous static return-page shells for Stripe's own OAuth-style redirect flow (must be reachable pre-auth since Stripe, not the driver's session, is doing the navigating). |
| `POST /ai/public-chat` (`routes/ai.py`) | 1 route | Named "public" — presumably deliberate (pre-login assistant chat). Not deepened here; `security.md`'s AI-provider rows (§3 vendor matrix in `integrations.md`) are the closer read for what this path can see/leak. |

## §3 Money-moving-heuristic routes with no idempotency-keyword hit (cross-check, not a finding list)

221 of the 253 money-heuristic routes have no "idempoten" hit in their first ~80 body lines. Given the heuristic's own stated limitation (§0 point 4 — a real idempotency mechanism like the ride-acceptance race guard or a DB unique constraint carries no such keyword), **this is not a list of unprotected money routes** — it is raw material for whoever runs `spinr-money-auditor` next, so they don't have to re-grep the same ~250 files from scratch. Full list is in §4's table (`money=Y, idemp=N` rows); not reproduced again here to avoid a second copy of the same data going stale independently.

## §4 Full route table (722 definitions, grouped by source file)

Columns: Method | Path (best-effort resolved mount prefix — see §0/§1 on double-mounts; a route mounted twice is listed once here under its first-resolved mount, `/api/v1` preferred over `/api` where both exist) | file:line | Auth dependency (VERIFIED where matched; **NONE FOUND** flagged) | Rate-limited? | Idempotency (heuristic) | Money-moving (heuristic).


### `backend/documents.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/documents/drivers/{driver_id}` | 1094 | [Depends(get_admin_user)] | — | N | N |
| GET | `/documents/requirements` | 1036 | [Depends(get_admin_user)] | — | N | N |
| POST | `/documents/requirements` | 1043 | [Depends(get_admin_user)] | — | N | N |
| DELETE | `/documents/requirements/{req_id}` | 1083 | [Depends(get_admin_user)] | — | N | N |
| PUT | `/documents/requirements/{req_id}` | 1058 | [Depends(get_admin_user)] | — | N | N |
| POST | `/documents/{doc_id}/review` | 1109 | [Depends(get_admin_user)] | — | N | N |
| GET | `/drivers/documents` | 721 | Depends(get_current_user) | — | N | N |
| POST | `/drivers/documents` | 738 | Depends(get_current_user) | — | N | N |
| POST | `/drivers/documents/upload` | 901 | Depends(get_current_user) | — | N | N |
| DELETE | `/drivers/documents/{document_id}` | 995 | Depends(get_current_user) | — | N | N |
| GET | `/drivers/requirements` | 672 | Depends(get_current_user) | — | N | N |
| GET | `/api/v1/documents/{file_id}` | 1291 | Depends(get_current_user) | — | N | N |
| POST | `/upload` | 1201 | Depends(get_current_user) | — | N | N |

### `backend/features.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| PUT | `/service-areas/{area_id}/surge/auto` | 436 | [Depends(get_admin_user)] | — | N | Y |
| GET | `/areas/{area_id}/fees` | 499 | [Depends(get_admin_user)] | — | N | N |
| POST | `/areas/{area_id}/fees` | 508 | [Depends(get_admin_user)]; Depends(get_admin_user) | — | N | N |
| DELETE | `/areas/{area_id}/fees/{fee_id}` | 565 | [Depends(get_admin_user)]; Depends(get_admin_user) | — | N | N |
| PUT | `/areas/{area_id}/fees/{fee_id}` | 546 | [Depends(get_admin_user)]; Depends(get_admin_user) | — | N | N |
| GET | `/areas/{area_id}/tax` | 619 | [Depends(get_admin_user)] | — | N | N |
| PUT | `/areas/{area_id}/tax` | 573 | [Depends(get_admin_user)]; Depends(get_admin_user) | — | N | N |
| GET | `/areas/{area_id}/vehicle-pricing` | 635 | [Depends(get_admin_user)] | — | N | Y |
| PUT | `/drivers/{driver_id}/area` | 643 | [Depends(get_admin_user)]; Depends(get_admin_user) | — | N | Y |
| GET | `/area-config` | 961 | **NONE FOUND** | — | N | N |
| GET | `/faqs` | 344 | **NONE FOUND** | — | N | Y |
| GET | `/rides/airport-fee` | 261 | **NONE FOUND** | — | N | Y |
| GET | `/rides/fare-estimate` | 939 | **NONE FOUND** | — | N | Y |
| POST | `/rides/schedule` | 1025 | Depends(get_current_user) | — | N | Y |
| GET | `/rides/scheduled` | 1086 | Depends(get_current_user) | — | N | N |
| DELETE | `/rides/scheduled/{ride_id}` | 1093 | Depends(get_current_user) | — | N | N |
| POST | `/rides/{ride_id}/share` | 1164 | Depends(get_current_user) | — | N | N |
| POST | `/rides/{ride_id}/stops` | 1111 | Depends(get_current_user) | — | N | N |
| PUT | `/rides/{ride_id}/stops/{stop_id}/complete` | 1139 | Depends(get_current_user) | — | N | N |
| GET | `/tickets` | 319 | Depends(get_current_user) | — | N | N |
| POST | `/tickets` | 278 | Depends(get_current_user) | — | N | N |
| POST | `/tickets/safety-report` | 300 | Depends(get_current_user) | — | N | N |
| GET | `/tickets/{ticket_id}` | 328 | Depends(get_current_user) | — | N | N |
| GET | `/trip/live/{token}` | 1203 | **NONE FOUND** | — | N | N |

### `backend/routes/addresses.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/addresses` | 23 | Depends(get_current_user) | — | N | N |
| POST | `/addresses` | 29 | Depends(get_current_user) | — | N | N |
| DELETE | `/addresses/{address_id}` | 54 | Depends(get_current_user) | — | N | N |

### `backend/routes/admin/ai_console.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/ai/chat` | 101 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @admin_ai_console_limit | N | N |
| GET | `/api/admin/ai/security-events` | 189 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/ai/users/{user_id}/conversations` | 160 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/ai/users/{user_id}/conversations/{conversation_id}/messages` | 169 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | — | N | N |

### `backend/routes/admin/analytics.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/analytics/cancellation-reasons` | 124 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/analytics/dashboard` | 561 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/analytics/demand-forecast` | 678 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/analytics/demand-forecast/summary` | 694 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/analytics/dispatch-latency` | 1154 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/analytics/driver-acceptance` | 224 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/analytics/driver-offer-stats` | 761 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/analytics/driver-offer-trends` | 895 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/analytics/efficiency` | 1339 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/analytics/financial` | 1435 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/analytics/marketplace-funnel` | 952 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/analytics/overview` | 440 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/analytics/retention-cohorts` | 1241 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/analytics/supply-utilization` | 1064 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/analytics/surge-history` | 711 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | Y |

### `backend/routes/admin/auth.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/auth/break-glass` | 1272 | **NONE FOUND** | @limiter.limit("5/hour") | N | N |
| POST | `/api/admin/auth/change-password` | 751 | **NONE FOUND** | @limiter.limit("3/minute") | N | N |
| POST | `/api/admin/auth/login` | 316 | **NONE FOUND** | @limiter.limit(_admin_login_rate_limit) | N | Y |
| POST | `/api/admin/auth/logout` | 609 | **NONE FOUND** | @limiter.limit("10/minute") | N | N |
| POST | `/api/admin/auth/logout-all` | 652 | **NONE FOUND** | @limiter.limit("5/minute") | N | N |
| POST | `/api/admin/auth/mfa/challenge` | 1176 | **NONE FOUND** | @limiter.limit("10/minute") | N | N |
| POST | `/api/admin/auth/mfa/confirm` | 1065 | **NONE FOUND** | @limiter.limit("5/minute") | N | N |
| POST | `/api/admin/auth/mfa/disable` | 1138 | **NONE FOUND** | @limiter.limit("3/minute") | N | N |
| POST | `/api/admin/auth/mfa/enroll` | 1054 | **NONE FOUND** | @limiter.limit("5/minute") | N | N |
| GET | `/api/admin/auth/mfa/status` | 1013 | **NONE FOUND** | — | N | N |
| POST | `/api/admin/auth/refresh` | 494 | **NONE FOUND** | @limiter.limit("20/minute") | N | Y |
| GET | `/api/admin/auth/session` | 259 | **NONE FOUND** | — | N | N |
| POST | `/api/admin/auth/unlock` | 1469 | Depends(get_admin_user) | @limiter.limit("10/minute") | Y | N |

### `backend/routes/admin/auto_payouts.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/auto-payouts/batches` | 76 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('earnings')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/auto-payouts/blocked-drivers` | 106 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('earnings')); Depends(get_admin_user) | — | N | Y |
| POST | `/api/admin/auto-payouts/run-now` | 145 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('earnings')); Depends(require_super_admin) | — | Y | Y |

### `backend/routes/admin/booking_import.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/bookings/import/commit` | 226 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @booking_import_commit_limit | N | Y |
| POST | `/api/admin/bookings/import/validate` | 200 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @booking_import_validate_limit | N | N |

### `backend/routes/admin/compliance.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/compliance/airport-trips` | 1607 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @limiter.limit("10/minute") | N | Y |
| GET | `/api/admin/compliance/driver-roster` | 733 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @limiter.limit("10/minute") | N | Y |
| GET | `/api/admin/compliance/gst-pst-remittance` | 515 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @limiter.limit("10/minute") | N | Y |
| GET | `/api/admin/compliance/insurance-billing-knight-archer` | 1444 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @limiter.limit("10/minute") | N | Y |
| GET | `/api/admin/compliance/insurance-billing-sgi` | 1409 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @limiter.limit("10/minute") | N | Y |
| GET | `/api/admin/compliance/saskatoon-city-trip-log` | 1816 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @limiter.limit("10/minute") | N | N |
| GET | `/api/admin/compliance/t4a-filer-handoff` | 981 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @limiter.limit("10/minute") | N | Y |

### `backend/routes/admin/data_transfer_export.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/data-transfer/export` | 308 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @data_transfer_export_limit | N | N |

### `backend/routes/admin/data_transfer_import.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/data-transfer/import/commit` | 90 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @data_transfer_import_commit_limit | N | N |
| POST | `/api/admin/data-transfer/import/validate` | 71 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @data_transfer_import_validate_limit | N | N |

### `backend/routes/admin/data_transfer_jobs.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/data-transfer/jobs` | 51 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @data_transfer_jobs_limit | N | N |
| GET | `/api/admin/data-transfer/jobs/{job_id}` | 79 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @data_transfer_jobs_limit | N | N |
| GET | `/api/admin/data-transfer/jobs/{job_id}/download` | 99 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @data_transfer_jobs_limit | N | N |

### `backend/routes/admin/data_transfer_search.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/data-transfer/search` | 108 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @data_transfer_search_limit | N | N |

### `backend/routes/admin/dispute_evidence_submission.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/disputes/{dispute_id}/submit-evidence` | 69 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(require_super_admin) | — | Y | Y |

### `backend/routes/admin/dispute_pack_download.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/rides/{ride_id}/dispute-pack` | 61 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')); Depends(get_admin_user) | — | N | Y |

### `backend/routes/admin/documents.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/documents/drivers/{driver_id}` | 250 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('documents')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/documents/pending` | 211 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('documents')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/documents/requirements` | 126 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('documents')) | — | N | N |
| POST | `/api/admin/documents/requirements` | 133 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('documents')); Depends(get_admin_user) | — | N | N |
| DELETE | `/api/admin/documents/requirements/{requirement_id}` | 191 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('documents')); Depends(get_admin_user) | — | N | N |
| PUT | `/api/admin/documents/requirements/{requirement_id}` | 159 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('documents')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/documents/upload` | 662 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('documents')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/documents/{document_id}/review` | 301 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('documents')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/documents/{document_id}/view` | 74 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('documents')); Depends(get_admin_user) | — | N | N |

### `backend/routes/admin/driver_appeals.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/driver-appeals` | 55 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/driver-appeals/stats` | 66 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/driver-appeals/{appeal_id}/resolve` | 71 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |

### `backend/routes/admin/driver_distance.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/drivers/{driver_id}/distance-logs` | 212 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/drivers/{driver_id}/distance-travelled` | 69 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |

### `backend/routes/admin/driver_dormancy.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/drivers/dormancy/commit` | 90 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @driver_dormancy_commit_limit | N | N |
| POST | `/api/admin/drivers/dormancy/preview` | 76 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @driver_dormancy_preview_limit | N | N |

### `backend/routes/admin/driver_import.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/drivers/import/commit` | 156 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | @driver_import_commit_limit | N | N |
| POST | `/api/admin/drivers/import/validate` | 135 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |

### `backend/routes/admin/driver_statements.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/drivers/statements/recompute-totals` | 273 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/drivers/{driver_id}/statements` | 119 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | Y |
| POST | `/api/admin/drivers/{driver_id}/statements/email` | 193 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | @admin_statement_email_limit | N | N |
| GET | `/api/admin/drivers/{driver_id}/statements/pdf` | 153 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | @admin_statement_download_limit | N | N |

### `backend/routes/admin/drivers.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/drivers` | 718 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')) | — | N | N |
| GET | `/api/admin/drivers/approval-queue` | 1240 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')) | — | N | N |
| POST | `/api/admin/drivers/decals/generate-pdf` | 4363 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/drivers/expiring` | 1504 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')) | — | N | N |
| DELETE | `/api/admin/drivers/notes/{note_id}` | 2401 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/drivers/notify-missing-license` | 1708 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/drivers/refresh-stripe-kyc` | 3910 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | Y |
| POST | `/api/admin/drivers/refresh-stripe-payouts` | 3770 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | Y |
| POST | `/api/admin/drivers/search` | 874 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/drivers/stats` | 889 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')) | — | N | N |
| PUT | `/api/admin/drivers/{driver_id}` | 1771 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/drivers/{driver_id}/action` | 2117 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/drivers/{driver_id}/activity` | 2412 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')) | — | N | N |
| PUT | `/api/admin/drivers/{driver_id}/area` | 4238 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/drivers/{driver_id}/completeness` | 4439 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/drivers/{driver_id}/daily-activity` | 4276 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/drivers/{driver_id}/daily-stats` | 4210 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')) | — | N | N |
| GET | `/api/admin/drivers/{driver_id}/live-stats` | 2476 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')) | — | N | N |
| GET | `/api/admin/drivers/{driver_id}/location-trail` | 4253 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')) | — | N | N |
| GET | `/api/admin/drivers/{driver_id}/notes` | 2361 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')) | — | N | N |
| POST | `/api/admin/drivers/{driver_id}/notes` | 2374 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/drivers/{driver_id}/nudge-expiry` | 1619 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/drivers/{driver_id}/payouts-summary` | 3159 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')) | — | N | Y |
| POST | `/api/admin/drivers/{driver_id}/photo` | 2068 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/drivers/{driver_id}/photo-review` | 2023 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/drivers/{driver_id}/referrals` | 2706 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/drivers/{driver_id}/refresh-stripe-kyc` | 3446 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | Y |
| POST | `/api/admin/drivers/{driver_id}/refresh-stripe-payouts` | 3515 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | Y |
| POST | `/api/admin/drivers/{driver_id}/reveal-sin` | 3986 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | @admin_sin_reveal_limit | N | Y |
| GET | `/api/admin/drivers/{driver_id}/rides` | 2425 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')) | — | N | N |
| PUT | `/api/admin/drivers/{driver_id}/status-override` | 2283 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/drivers/{driver_id}/training` | 2715 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/drivers/{driver_id}/update-sin` | 4074 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | @admin_sin_update_limit | N | Y |
| GET | `/api/admin/drivers/{driver_id}/vehicle-history` | 2108 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/drivers/{driver_id}/verify` | 1972 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/referrals/analytics` | 2920 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/referrals/failed-claims` | 3056 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | Y |
| POST | `/api/admin/referrals/failed-claims/{referee_user_id}/requeue` | 3093 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/referrals/leaderboard` | 2754 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/referrals/pairs` | 3124 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/referrals/rider-leaderboard` | 2831 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |

### `backend/routes/admin/export_approvals.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/export-approvals/pending` | 47 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @export_approvals_list_limit | N | N |
| POST | `/api/admin/export-approvals/{request_id}/approve` | 55 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @export_approvals_decide_limit | N | N |
| POST | `/api/admin/export-approvals/{request_id}/deny` | 83 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @export_approvals_decide_limit | N | N |

### `backend/routes/admin/faqs.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/faqs` | 70 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')) | — | N | N |
| POST | `/api/admin/faqs` | 91 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')); Depends(get_admin_user) | — | N | N |
| DELETE | `/api/admin/faqs/{faq_id}` | 152 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')); Depends(get_admin_user) | — | N | N |
| PUT | `/api/admin/faqs/{faq_id}` | 118 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/notifications` | 230 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')) | — | N | N |
| POST | `/api/admin/notifications/send` | 163 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')); Depends(get_admin_user) | @admin_mass_notify_limit | N | N |

### `backend/routes/admin/incentives.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/incentives` | 60 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')) | — | N | N |
| POST | `/api/admin/incentives` | 123 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/incentives/claims/stats` | 83 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')) | — | N | N |
| DELETE | `/api/admin/incentives/{incentive_id}` | 233 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/incentives/{incentive_id}` | 107 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')) | — | N | N |
| PATCH | `/api/admin/incentives/{incentive_id}` | 165 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')); Depends(get_admin_user) | — | N | N |
| PATCH | `/api/admin/incentives/{incentive_id}/toggle` | 197 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')); Depends(get_admin_user) | — | N | N |

### `backend/routes/admin/legacy_driver_import.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/legacy-drivers/backfill-created-at` | 299 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/legacy-drivers/backfill-orphaned` | 257 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | Y | N |
| POST | `/api/admin/legacy-drivers/import/commit` | 178 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | @legacy_mongo_driver_import_commit_limit | N | N |
| POST | `/api/admin/legacy-drivers/import/validate` | 157 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |

### `backend/routes/admin/legacy_duration_estimated_backfill.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/legacy/duration-estimated-backfill/commit` | 113 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @duration_estimated_backfill_commit_limit | N | N |
| POST | `/api/admin/legacy/duration-estimated-backfill/preview` | 99 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @duration_estimated_backfill_preview_limit | N | N |

### `backend/routes/admin/legacy_id_crosswalk.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/legacy/id-crosswalk-backfill/commit` | 91 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @legacy_id_crosswalk_backfill_commit_limit | N | N |
| POST | `/api/admin/legacy/id-crosswalk-backfill/preview` | 77 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @legacy_id_crosswalk_backfill_preview_limit | N | N |

### `backend/routes/admin/legacy_saved_address_backfill.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/riders/saved-address-backfill/commit` | 152 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('users')); Depends(get_admin_user) | @legacy_saved_address_backfill_commit_limit | N | N |
| POST | `/api/admin/riders/saved-address-backfill/validate` | 137 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('users')); Depends(get_admin_user) | — | N | N |

### `backend/routes/admin/legacy_sin_dob_backfill.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/legacy-drivers/sin-dob-backfill/commit` | 183 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @legacy_sin_dob_backfill_commit_limit | N | N |
| POST | `/api/admin/legacy-drivers/sin-dob-backfill/validate` | 161 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | — | N | N |

### `backend/routes/admin/legacy_vehicle_history_backfill.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/legacy-drivers/vehicle-history-backfill/commit` | 180 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | @legacy_vehicle_history_backfill_commit_limit | N | N |
| POST | `/api/admin/legacy-drivers/vehicle-history-backfill/validate` | 159 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('drivers')); Depends(get_admin_user) | — | N | N |

### `backend/routes/admin/legal_documents.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/legal-documents` | 45 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('documents')) | — | N | N |
| PUT | `/api/admin/legal-documents` | 52 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('documents')); Depends(get_admin_user) | — | N | N |

### `backend/routes/admin/maintenance.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/agent-actions` | 295 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(require_module) | — | N | N |
| GET | `/api/admin/audit-logs` | 208 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(require_module) | — | N | N |
| GET | `/api/admin/audit-logs/top-actors` | 263 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(require_module) | — | N | N |
| POST | `/api/admin/audit/pii-reveal` | 330 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/maintenance/cleanup-location-history` | 63 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/maintenance/rollup-driver-daily` | 123 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | Y | N |

### `backend/routes/admin/messaging.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/cloud-messaging` | 408 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('notifications')) | — | N | N |
| GET | `/api/admin/cloud-messaging/audience-preview` | 376 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('notifications')) | — | N | N |
| POST | `/api/admin/cloud-messaging/send` | 280 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('notifications')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/cloud-messaging/stats` | 437 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('notifications')) | — | N | N |
| DELETE | `/api/admin/cloud-messaging/{message_id}` | 543 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('notifications')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/marketing/suppressions` | 476 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('notifications')) | — | N | N |
| POST | `/api/admin/marketing/suppressions` | 501 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('notifications')); Depends(get_admin_user) | — | N | N |
| DELETE | `/api/admin/marketing/suppressions/{suppression_id}` | 521 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('notifications')); Depends(get_admin_user) | — | N | N |

### `backend/routes/admin/migration_data_quality.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/legacy/data-quality-scan/commit` | 87 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @data_quality_scan_commit_limit | N | N |
| POST | `/api/admin/legacy/data-quality-scan/preview` | 73 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @data_quality_scan_preview_limit | N | N |

### `backend/routes/admin/migration_driver_repair.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/legacy/driver-repair/commit` | 85 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @driver_repair_commit_limit | N | N |
| POST | `/api/admin/legacy/driver-repair/preview` | 71 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @driver_repair_preview_limit | N | N |

### `backend/routes/admin/migration_status.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/migration-status` | 41 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | — | N | N |

### `backend/routes/admin/monitoring.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/monitoring/dispatch-geo` | 636 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/monitoring/dispatch-geo/rebuild` | 657 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/monitoring/drivers` | 282 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/monitoring/email-deliverability` | 356 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/monitoring/health` | 844 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/monitoring/infrastructure` | 752 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/monitoring/migrate-profile-images` | 290 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | Y | N |
| GET | `/api/admin/monitoring/redis` | 443 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | Y | Y |
| GET | `/api/admin/monitoring/redis/connectivity` | 504 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/monitoring/redis/flush-prefix` | 552 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/monitoring/rides` | 426 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/monitoring/websockets` | 703 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | N |

### `backend/routes/admin/pre_launch_flag.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/legacy/pre-launch-flag/commit` | 93 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @pre_launch_flag_commit_limit | N | N |
| POST | `/api/admin/legacy/pre-launch-flag/preview` | 79 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @pre_launch_flag_preview_limit | N | N |

### `backend/routes/admin/promotions.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/promotions` | 87 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('promotions')) | — | N | N |
| POST | `/api/admin/promotions` | 153 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('promotions')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/promotions/stats` | 342 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('promotions')) | — | N | N |
| GET | `/api/admin/promotions/usage` | 256 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('promotions')) | — | N | N |
| DELETE | `/api/admin/promotions/{promotion_id}` | 439 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('promotions')); Depends(get_admin_user) | — | N | N |
| PUT | `/api/admin/promotions/{promotion_id}` | 405 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('promotions')); Depends(get_admin_user) | — | N | N |

### `backend/routes/admin/rider_import.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/riders/created-at-backfill` | 152 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('users')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/riders/import/commit` | 113 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('users')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/riders/import/validate` | 100 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('users')); Depends(get_admin_user) | — | N | N |

### `backend/routes/admin/rides.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/earnings` | 2565 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')) | — | N | Y |
| GET | `/api/admin/earnings/overview` | 2800 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')) | — | N | Y |
| GET | `/api/admin/earnings/rides` | 2599 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')) | — | N | Y |
| GET | `/api/admin/export/drivers` | 3090 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/export/rides` | 3031 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/payouts` | 3710 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')) | — | N | Y |
| POST | `/api/admin/payouts/bulk-retry` | 3853 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')); Depends(get_admin_user) | — | N | Y |
| POST | `/api/admin/payouts/close-period` | 3984 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/payouts/overview` | 3416 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')) | — | N | Y |
| GET | `/api/admin/payouts/stats` | 3751 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')) | — | N | Y |
| GET | `/api/admin/payouts/{payout_id}` | 3775 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')); Depends(get_admin_user) | — | N | Y |
| POST | `/api/admin/payouts/{payout_id}/retry` | 3801 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/places/autocomplete` | 984 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')); Depends(get_admin_user) | @limiter.limit("60/minute") | N | N |
| GET | `/api/admin/places/details` | 1033 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')); Depends(get_admin_user) | @limiter.limit("60/minute") | N | N |
| POST | `/api/admin/promo/preview` | 1115 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/rides` | 246 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')) | — | N | N |
| GET | `/api/admin/rides/active` | 330 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')) | — | N | Y |
| POST | `/api/admin/rides/create` | 1164 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/rides/export` | 291 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/rides/fare-estimate` | 1075 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/rides/financials` | 1678 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/rides/heatmap-data` | 2479 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')) | — | N | Y |
| GET | `/api/admin/rides/held-for-review` | 452 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')); Depends(get_admin_user) | — | N | Y |
| POST | `/api/admin/rides/regenerate-imported-routes` | 4286 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/rides/regenerate-imported-snapshots` | 4072 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/rides/stats` | 1552 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')) | — | N | Y |
| GET | `/api/admin/rides/trend` | 1628 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')) | — | N | N |
| GET | `/api/admin/rides/unpaid` | 391 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')); Depends(get_admin_user) | — | N | Y |
| POST | `/api/admin/rides/{ride_id}/cancel` | 603 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/rides/{ride_id}/complete` | 830 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')); Depends(get_admin_user) | — | Y | Y |
| GET | `/api/admin/rides/{ride_id}/details` | 1817 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')); Depends(get_admin_user) | — | N | Y |
| POST | `/api/admin/rides/{ride_id}/held-for-review/release` | 524 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')); Depends(get_admin_user) | — | N | Y |
| POST | `/api/admin/rides/{ride_id}/held-for-review/waive` | 563 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/rides/{ride_id}/invoice` | 1844 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')) | — | N | Y |
| GET | `/api/admin/rides/{ride_id}/live` | 1835 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')) | — | N | N |
| GET | `/api/admin/rides/{ride_id}/location-trail` | 1828 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')) | — | N | N |
| GET | `/api/admin/rides/{ride_id}/route-map.png` | 2452 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/rides/{ride_id}/send-invoice` | 1986 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')); Depends(get_admin_user) | @limiter.limit("10/minute") | Y | Y |
| POST | `/api/admin/rides/{ride_id}/send-receipt` | 1926 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')); Depends(get_admin_user) | @limiter.limit("10/minute") | N | Y |
| GET | `/api/admin/stats` | 1467 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('rides')) | — | N | Y |

### `backend/routes/admin/safety.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/safety/incidents` | 55 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')) | — | N | N |
| POST | `/api/admin/safety/incidents` | 265 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/safety/incidents/{incident_id}` | 165 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')) | — | N | Y |
| PATCH | `/api/admin/safety/incidents/{incident_id}` | 327 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/safety/incidents/{incident_id}/merge` | 396 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')); Depends(get_admin_user) | — | N | N |

### `backend/routes/admin/sentry.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/sentry/config` | 491 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(require_super_admin) | @limiter.limit(_READ_RATE_LIMIT) | N | N |
| GET | `/api/admin/sentry/issues` | 673 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(require_super_admin) | @limiter.limit(_READ_RATE_LIMIT) | N | N |
| GET | `/api/admin/sentry/issues/{issue_id}` | 794 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(require_super_admin) | @limiter.limit(_READ_RATE_LIMIT) | N | N |
| POST | `/api/admin/sentry/issues/{issue_id}/status` | 861 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(require_super_admin) | @limiter.limit(_WRITE_RATE_LIMIT) | N | N |

### `backend/routes/admin/service_areas.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/airport-zones/diagnostic` | 495 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')) | — | N | Y |
| GET | `/api/admin/areas/{area_id}/fees` | 1154 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')) | — | N | N |
| POST | `/api/admin/areas/{area_id}/fees` | 1161 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')); Depends(get_admin_user) | — | N | N |
| DELETE | `/api/admin/areas/{area_id}/fees/{fee_id}` | 1213 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')); Depends(get_admin_user) | — | N | N |
| PUT | `/api/admin/areas/{area_id}/fees/{fee_id}` | 1184 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/areas/{area_id}/tax` | 1221 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')) | — | N | N |
| PUT | `/api/admin/areas/{area_id}/tax` | 1246 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/areas/{area_id}/vehicle-pricing` | 1296 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')) | — | N | Y |
| GET | `/api/admin/service-areas` | 416 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')) | — | N | N |
| POST | `/api/admin/service-areas` | 587 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')); Depends(get_admin_user) | — | N | Y |
| DELETE | `/api/admin/service-areas/{area_id}` | 1059 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')); Depends(get_admin_user) | — | N | Y |
| PUT | `/api/admin/service-areas/{area_id}` | 681 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/service-areas/{area_id}/heatmap-config` | 435 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')) | — | N | N |
| PUT | `/api/admin/service-areas/{area_id}/surge` | 1072 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/service-areas/{area_id}/tax-history` | 1040 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/surge/status` | 1136 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')) | — | N | Y |

### `backend/routes/admin/settings.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/ai/catalog` | 726 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('settings')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/settings` | 719 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('settings')); Depends(get_admin_user) | — | N | N |
| PUT | `/api/admin/settings` | 765 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('settings')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/settings/heatmap` | 963 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('settings')); Depends(get_admin_user) | — | N | N |
| PUT | `/api/admin/settings/heatmap` | 977 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('settings')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/settings/reveal/{field}` | 740 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('settings')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/settings/ride-offer-sound` | 841 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('settings')); Depends(get_admin_user) | — | N | N |

### `backend/routes/admin/sgi_forms.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/data-transfer/sgi-forms/documents` | 320 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @data_transfer_export_limit | N | N |
| POST | `/api/admin/data-transfer/sgi-forms/generate` | 129 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/data-transfer/sgi-forms/package` | 491 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @data_transfer_export_limit | N | N |
| GET | `/api/admin/data-transfer/sgi-forms/removal-queue` | 257 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | — | N | N |

### `backend/routes/admin/staff.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/staff` | 168 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('staff')); Depends(require_role) | — | N | N |
| POST | `/api/admin/staff` | 205 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('staff')); Depends(require_role) | — | N | N |
| GET | `/api/admin/staff/modules/list` | 279 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('staff')) | — | N | N |
| DELETE | `/api/admin/staff/{staff_id}` | 456 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('staff')); Depends(require_role) | @admin_staff_delete_limit | N | N |
| GET | `/api/admin/staff/{staff_id}` | 288 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('staff')); Depends(require_role) | — | N | N |
| PUT | `/api/admin/staff/{staff_id}` | 304 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('staff')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/staff/{staff_id}/mfa-reset` | 398 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('staff')); Depends(require_role) | — | N | N |

### `backend/routes/admin/stripe_connect_ledger.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/stripe/connect-ledger/sync` | 84 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | — | N | Y |

### `backend/routes/admin/stripe_events.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/stripe-events/stuck` | 109 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(require_super_admin) | — | N | Y |
| GET | `/api/admin/stripe-events/stuck/count` | 146 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(require_super_admin) | — | N | Y |
| GET | `/api/admin/stripe-events/{event_id}` | 160 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(require_super_admin) | — | N | Y |
| POST | `/api/admin/stripe-events/{event_id}/dismiss` | 268 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(require_super_admin) | — | N | Y |
| POST | `/api/admin/stripe-events/{event_id}/replay` | 188 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(require_super_admin) | — | N | Y |

### `backend/routes/admin/stripe_import.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/stripe/import/commit` | 146 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | — | N | Y |
| POST | `/api/admin/stripe/import/discover` | 354 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/stripe/import/status` | 337 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | — | N | Y |
| POST | `/api/admin/stripe/import/update-driver` | 252 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | — | N | Y |
| POST | `/api/admin/stripe/import/validate` | 131 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | — | N | Y |

### `backend/routes/admin/stripe_mode_audit.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/stripe/customer-emails/backfill` | 291 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/stripe/mode-audit` | 110 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | — | N | Y |
| POST | `/api/admin/stripe/mode-audit/probe` | 157 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | — | N | Y |

### `backend/routes/admin/stripe_payout_sync.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/stripe/payout-sync/commit` | 122 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | — | N | Y |
| POST | `/api/admin/stripe/payout-sync/validate` | 110 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | — | N | Y |

### `backend/routes/admin/subscriptions.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/offer-analytics` | 477 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('dashboard')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/driver-subscriptions` | 122 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('earnings')) | — | N | N |
| PUT | `/api/admin/service-areas/{area_id}/subscription-tax` | 420 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('earnings')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/subscription-plans` | 59 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('earnings')) | — | N | N |
| POST | `/api/admin/subscription-plans` | 66 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('earnings')); Depends(get_admin_user) | — | N | Y |
| DELETE | `/api/admin/subscription-plans/{plan_id}` | 111 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('earnings')); Depends(get_admin_user) | — | N | N |
| PUT | `/api/admin/subscription-plans/{plan_id}` | 94 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('earnings')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/subscription-stats` | 131 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('earnings')) | — | N | Y |
| GET | `/api/admin/subscription/payments` | 285 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('earnings')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/subscription/payments/{payment_id}/invoice.pdf` | 644 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('earnings')); Depends(get_admin_user) | — | N | Y |
| POST | `/api/admin/subscription/payments/{payment_id}/resend-invoice` | 685 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('earnings')); Depends(get_admin_user) | — | N | Y |

### `backend/routes/admin/support.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/complaints` | 643 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')) | — | N | N |
| DELETE | `/api/admin/complaints/{complaint_id}` | 662 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')); Depends(get_admin_user) | — | N | N |
| PUT | `/api/admin/complaints/{complaint_id}/resolve` | 615 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/disputes` | 107 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')) | — | N | N |
| POST | `/api/admin/disputes` | 266 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/disputes/chargebacks` | 185 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')) | — | N | Y |
| GET | `/api/admin/disputes/stats` | 156 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')) | — | N | Y |
| DELETE | `/api/admin/disputes/{dispute_id}` | 351 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/disputes/{dispute_id}` | 289 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')) | — | N | N |
| PUT | `/api/admin/disputes/{dispute_id}` | 300 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')); Depends(get_admin_user) | — | N | Y |
| PUT | `/api/admin/disputes/{dispute_id}/resolve` | 327 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/flags` | 541 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')) | — | N | N |
| DELETE | `/api/admin/flags/{flag_id}` | 571 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')); Depends(get_admin_user) | — | N | N |
| PUT | `/api/admin/flags/{flag_id}/deactivate` | 561 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/rides/{ride_id}/complaint` | 582 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/rides/{ride_id}/flag` | 500 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/tickets` | 362 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')) | — | N | N |
| POST | `/api/admin/tickets` | 386 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')); Depends(get_admin_user) | — | N | N |
| DELETE | `/api/admin/tickets/{ticket_id}` | 489 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/tickets/{ticket_id}` | 407 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')) | — | N | N |
| PUT | `/api/admin/tickets/{ticket_id}` | 464 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/tickets/{ticket_id}/close` | 452 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/tickets/{ticket_id}/reply` | 420 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('support')); Depends(get_admin_user) | — | N | N |

### `backend/routes/admin/support_tickets.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/support-tickets/agents` | 449 | Depends(get_admin_user) [admin_router-level] (no module gate); Depends(require_module) | — | N | N |
| GET | `/api/admin/support-tickets/config` | 206 | Depends(get_admin_user) [admin_router-level] (no module gate); Depends(require_module) | — | N | N |
| PUT | `/api/admin/support-tickets/config` | 212 | Depends(get_admin_user) [admin_router-level] (no module gate); Depends(require_module) | — | N | N |
| POST | `/api/admin/support-tickets/config/test` | 260 | Depends(get_admin_user) [admin_router-level] (no module gate); Depends(require_module) | — | N | N |
| GET | `/api/admin/support-tickets/dashboard` | 352 | Depends(get_admin_user) [admin_router-level] (no module gate); Depends(require_module) | — | N | N |
| GET | `/api/admin/support-tickets/departments` | 457 | Depends(get_admin_user) [admin_router-level] (no module gate); Depends(require_module) | — | N | N |
| GET | `/api/admin/support-tickets/search` | 545 | Depends(get_admin_user) [admin_router-level] (no module gate); Depends(require_module) | — | N | N |
| GET | `/api/admin/support-tickets/service-areas` | 734 | Depends(get_admin_user) [admin_router-level] (no module gate); Depends(require_module) | — | N | N |
| POST | `/api/admin/support-tickets/sync` | 244 | Depends(get_admin_user) [admin_router-level] (no module gate); Depends(require_module) | — | N | N |
| GET | `/api/admin/support-tickets/tickets` | 502 | Depends(get_admin_user) [admin_router-level] (no module gate); Depends(require_module) | — | N | N |
| POST | `/api/admin/support-tickets/tickets` | 478 | Depends(get_admin_user) [admin_router-level] (no module gate); Depends(require_module) | — | N | N |
| GET | `/api/admin/support-tickets/tickets/{ticket_id}` | 586 | Depends(get_admin_user) [admin_router-level] (no module gate); Depends(require_module) | — | N | N |
| PATCH | `/api/admin/support-tickets/tickets/{ticket_id}` | 682 | Depends(get_admin_user) [admin_router-level] (no module gate); Depends(require_module) | — | N | N |
| POST | `/api/admin/support-tickets/tickets/{ticket_id}/ai-suggest-reply` | 845 | Depends(get_admin_user) [admin_router-level] (no module gate); Depends(require_module) | @admin_ai_suggest_limit | N | N |
| POST | `/api/admin/support-tickets/tickets/{ticket_id}/comment` | 658 | Depends(get_admin_user) [admin_router-level] (no module gate); Depends(require_module) | — | N | N |
| POST | `/api/admin/support-tickets/tickets/{ticket_id}/reply` | 618 | Depends(get_admin_user) [admin_router-level] (no module gate); Depends(require_module) | — | N | N |
| GET | `/api/admin/support-tickets/tickets/{ticket_id}/service-area` | 746 | Depends(get_admin_user) [admin_router-level] (no module gate); Depends(require_module) | — | N | N |
| PUT | `/api/admin/support-tickets/tickets/{ticket_id}/service-area` | 768 | Depends(get_admin_user) [admin_router-level] (no module gate); Depends(require_module) | — | N | N |
| POST | `/api/admin/support-tickets/tickets/{ticket_id}/tags` | 704 | Depends(get_admin_user) [admin_router-level] (no module gate); Depends(require_module) | — | N | N |
| GET | `/api/admin/support-tickets/tickets/{ticket_id}/threads` | 594 | Depends(get_admin_user) [admin_router-level] (no module gate); Depends(require_module) | — | N | N |
| GET | `/api/admin/support-tickets/tickets/{ticket_id}/threads/{thread_id}` | 602 | Depends(get_admin_user) [admin_router-level] (no module gate); Depends(require_module) | — | N | N |
| GET | `/api/admin/support-tickets/trends` | 406 | Depends(get_admin_user) [admin_router-level] (no module gate); Depends(require_module) | — | N | N |

### `backend/routes/admin/tax_id_import.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/tax-ids/import/commit` | 423 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @tax_id_import_commit_limit | N | N |
| POST | `/api/admin/tax-ids/import/prepare-commit` | 489 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @tax_id_import_commit_limit | N | N |
| POST | `/api/admin/tax-ids/import/prepare-validate` | 445 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @tax_id_import_validate_limit | N | N |
| POST | `/api/admin/tax-ids/import/validate` | 407 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @tax_id_import_validate_limit | N | N |

### `backend/routes/admin/users.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/dsars` | 528 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('users')); Depends(get_admin_user) | — | N | N |
| PATCH | `/api/admin/dsars/{request_id}/status` | 562 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('users')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/export/users` | 475 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('users')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/users` | 68 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('users')) | — | N | N |
| POST | `/api/admin/users/search` | 152 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('users')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/users/{user_id}` | 229 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('users')); Depends(get_admin_user) | — | N | Y |
| PATCH | `/api/admin/users/{user_id}/role` | 395 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('users')); Depends(get_admin_user) | — | N | N |
| PUT | `/api/admin/users/{user_id}/status` | 267 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('users')); Depends(get_admin_user) | — | N | N |

### `backend/routes/admin/vehicle_fleet.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/fare-configs` | 450 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('vehicle_types')) | — | N | Y |
| POST | `/api/admin/fare-configs` | 457 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('vehicle_types')); Depends(get_admin_user) | — | N | Y |
| DELETE | `/api/admin/fare-configs/{config_id}` | 513 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('vehicle_types')); Depends(get_admin_user) | — | N | Y |
| PUT | `/api/admin/fare-configs/{config_id}` | 482 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('vehicle_types')); Depends(get_admin_user) | — | N | Y |
| GET | `/api/admin/lost-and-found` | 612 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('vehicle_types')) | — | N | N |
| DELETE | `/api/admin/lost-and-found/{item_id}` | 682 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('vehicle_types')); Depends(get_admin_user) | — | N | N |
| PUT | `/api/admin/lost-and-found/{item_id}` | 653 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('vehicle_types')); Depends(get_admin_user) | — | N | N |
| PUT | `/api/admin/lost-and-found/{item_id}/resolve` | 591 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('vehicle_types')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/rides/{ride_id}/lost-and-found` | 526 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('vehicle_types')); Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/vehicle-types` | 168 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('vehicle_types')) | — | N | N |
| POST | `/api/admin/vehicle-types` | 175 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('vehicle_types')); Depends(get_admin_user) | — | N | Y |
| DELETE | `/api/admin/vehicle-types/{type_id}` | 380 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('vehicle_types')); Depends(get_admin_user) | — | N | Y |
| PUT | `/api/admin/vehicle-types/{type_id}` | 199 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('vehicle_types')); Depends(get_admin_user) | — | N | Y |
| POST | `/api/admin/vehicle-types/{type_id}/upload-illustration` | 239 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('vehicle_types')); Depends(get_admin_user) | — | N | Y |
| POST | `/api/admin/vehicle-types/{type_id}/upload-marker` | 312 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('vehicle_types')); Depends(get_admin_user) | — | N | Y |

### `backend/routes/admin/venues.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/venues` | 46 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')); Depends(get_admin_user) | — | N | N |
| POST | `/api/admin/venues` | 77 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')); Depends(get_admin_user) | — | N | N |
| DELETE | `/api/admin/venues/{venue_id}` | 102 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')); Depends(get_admin_user) | — | N | N |
| PUT | `/api/admin/venues/{venue_id}` | 88 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('service_areas')); Depends(get_admin_user) | — | N | N |

### `backend/routes/admin/wallet.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/wallet/credit` | 128 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('earnings')); Depends(get_admin_user) | @admin_wallet_limit | Y | Y |
| POST | `/api/admin/wallet/debit` | 231 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('earnings')); Depends(get_admin_user) | @admin_wallet_limit | Y | Y |
| GET | `/api/admin/wallet/{user_id}` | 78 | Depends(get_admin_user) [admin_router-level] + Depends(require_module('earnings')); Depends(get_admin_user) | — | N | Y |

### `backend/routes/admin/wallet_import.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/wallets/import/commit` | 182 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @wallet_import_commit_limit | N | Y |
| POST | `/api/admin/wallets/import/validate` | 164 | Depends(get_admin_user) [admin_router-level] + Depends(require_super_admin); Depends(get_admin_user) | @wallet_import_validate_limit | N | Y |

### `backend/routes/ai.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/ai/chat` | 162 | Depends(get_current_user_active_session) | @ai_chat_limit | N | N |
| GET | `/ai/config` | 144 | Depends(get_current_user) | — | N | N |
| GET | `/ai/conversations` | 243 | Depends(get_current_user_active_session) | — | N | N |
| DELETE | `/ai/conversations/{conversation_id}` | 258 | Depends(get_current_user_active_session) | — | N | N |
| GET | `/ai/conversations/{conversation_id}/messages` | 248 | Depends(get_current_user_active_session) | — | N | N |
| POST | `/ai/public-chat` | 208 | **NONE FOUND** | @ai_public_chat_limit | N | N |

### `backend/routes/auth.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/v1/auth/firebase` | 1543 | **NONE FOUND** | @limiter.limit("10/minute") | N | N |
| POST | `/api/v1/auth/logout` | 2229 | Depends(get_current_user), Depends(get_token_session_id) | @limiter.limit("3/minute") | N | N |
| POST | `/api/v1/auth/logout-all` | 2525 | Depends(get_current_user) | @limiter.limit("5/minute") | N | N |
| GET | `/api/v1/auth/me` | 1789 | Depends(get_current_user) | — | N | N |
| POST | `/api/v1/auth/reactivate` | 1423 | **NONE FOUND** | @limiter.limit("5/minute") | Y | N |
| POST | `/api/v1/auth/refresh` | 1897 | **NONE FOUND** | @limiter.limit("20/minute") | N | N |
| POST | `/api/v1/auth/send-email-otp` | 766 | **NONE FOUND** | @limiter.limit("3/minute") | N | N |
| POST | `/api/v1/auth/send-otp` | ? | **NONE FOUND** | — | N | N |
| POST | `/api/v1/auth/verify-email-otp` | 879 | **NONE FOUND** | @limiter.limit("5/minute") | N | N |
| POST | `/api/v1/auth/verify-otp` | 952 | **NONE FOUND** | @limiter.limit("5/minute") | N | N |

### `backend/routes/branding.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/branding/spinr-logo.png` | 76 | **NONE FOUND** | @default_limiter.limit("120/minute") | N | N |

### `backend/routes/corporate_accounts.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/admin/corporate-accounts` | 198 | [Depends(require_module('corporate_accounts'))]; Depends(get_current_admin) | — | N | N |
| POST | `/admin/corporate-accounts` | 604 | [Depends(require_module('corporate_accounts'))]; Depends(get_current_admin) | — | N | N |
| GET | `/admin/corporate-accounts/kyb-reverification-due` | 261 | [Depends(require_module('corporate_accounts'))]; Depends(get_current_admin) | — | N | N |
| DELETE | `/admin/corporate-accounts/{account_id}` | 796 | [Depends(require_module('corporate_accounts'))]; Depends(get_current_admin) | — | N | N |
| GET | `/admin/corporate-accounts/{account_id}` | 698 | [Depends(require_module('corporate_accounts'))]; Depends(get_current_admin) | — | N | N |
| PUT | `/admin/corporate-accounts/{account_id}` | 728 | [Depends(require_module('corporate_accounts'))]; Depends(get_current_admin) | — | N | N |
| GET | `/admin/corporate-accounts/{company_id}/billing/statements/{month}/pdf` | 553 | [Depends(require_module('corporate_accounts'))]; Depends(get_current_admin) | — | N | Y |
| POST | `/admin/corporate-accounts/{company_id}/kyb-document` | 335 | [Depends(require_module('corporate_accounts'))]; Depends(get_current_admin) | — | N | N |
| POST | `/admin/corporate-accounts/{company_id}/kyb-review` | 374 | [Depends(require_module('corporate_accounts'))]; Depends(get_current_admin) | — | N | Y |
| POST | `/admin/corporate-accounts/{company_id}/kyb-upload-url` | 308 | [Depends(require_module('corporate_accounts'))]; Depends(get_current_admin) | — | N | N |
| GET | `/admin/corporate-accounts/{company_id}/kyb/view` | 508 | [Depends(require_module('corporate_accounts'))]; Depends(get_current_admin) | — | N | N |
| POST | `/admin/corporate-accounts/{company_id}/status` | ? | [Depends(require_module('corporate_accounts'))] | — | N | N |

### `backend/routes/corporate_company.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/company/{company_id}/allowance-requests` | 579 | Depends(require_company_admin) | — | N | Y |
| POST | `/api/company/{company_id}/allowance-requests/{request_id}/decide` | 638 | Depends(require_company_admin) | — | N | Y |
| GET | `/api/company/{company_id}/allowances` | 512 | Depends(require_company_admin) | — | N | Y |
| GET | `/api/company/{company_id}/allowed-domains` | 592 | Depends(require_company_admin) | — | N | N |
| POST | `/api/company/{company_id}/allowed-domains` | 597 | Depends(require_company_admin) | — | N | N |
| DELETE | `/api/company/{company_id}/allowed-domains/{domain}` | 617 | Depends(require_company_admin) | — | N | Y |
| GET | `/api/company/{company_id}/billing/statements/{month}` | 1026 | Depends(require_company_admin) | — | N | Y |
| GET | `/api/company/{company_id}/billing/statements/{month}/pdf` | 1124 | Depends(require_company_admin) | — | N | Y |
| GET | `/api/company/{company_id}/billing/summary` | 981 | Depends(require_company_admin) | — | N | Y |
| GET | `/api/company/{company_id}/billing/transactions` | 1163 | Depends(require_company_admin) | — | Y | Y |
| GET | `/api/company/{company_id}/members` | 172 | Depends(require_company_admin) | — | N | N |
| POST | `/api/company/{company_id}/members/invite` | 184 | Depends(require_company_admin) | — | N | N |
| DELETE | `/api/company/{company_id}/members/{member_id}` | 491 | Depends(require_company_admin) | — | N | Y |
| PATCH | `/api/company/{company_id}/members/{member_id}` | 433 | Depends(require_company_admin) | — | N | N |
| GET | `/api/company/{company_id}/members/{member_id}/allowance` | 523 | Depends(require_company_admin) | — | N | Y |
| PATCH | `/api/company/{company_id}/members/{member_id}/allowance` | 557 | Depends(require_company_admin) | — | N | Y |
| PUT | `/api/company/{company_id}/members/{member_id}/allowance` | 535 | Depends(require_company_admin) | — | N | Y |
| GET | `/api/company/{company_id}/policy` | 704 | Depends(require_company_member) | — | N | N |
| PATCH | `/api/company/{company_id}/policy` | 792 | Depends(require_company_admin) | — | N | Y |
| PUT | `/api/company/{company_id}/policy` | 747 | Depends(require_company_admin) | — | Y | Y |
| GET | `/api/company/{company_id}/policy/affected-rides-count` | 718 | Depends(require_company_admin) | — | N | Y |
| POST | `/api/company/{company_id}/wallet/topup` | 1194 | Depends(require_company_admin) | — | Y | Y |

### `backend/routes/corporate_company_bookings.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/company/{company_id}/bookings` | 159 | Depends(require_company_member) | — | N | N |
| POST | `/api/company/{company_id}/bookings` | 136 | Depends(require_company_member) | @company_booking_limit | N | Y |
| GET | `/api/company/{company_id}/bookings/fare-estimate` | 326 | Depends(require_company_member) | — | N | Y |
| POST | `/api/company/{company_id}/bookings/{ride_id}/cancel` | 260 | Depends(require_company_member) | — | N | N |
| GET | `/api/company/{company_id}/sections` | 392 | Depends(require_company_member) | — | N | N |
| POST | `/api/company/{company_id}/sections` | 440 | Depends(require_company_admin) | — | N | N |
| DELETE | `/api/company/{company_id}/sections/{section_id}` | 515 | Depends(require_company_admin) | — | N | N |
| PATCH | `/api/company/{company_id}/sections/{section_id}` | 476 | Depends(require_company_admin) | — | N | N |

### `backend/routes/corporate_company_kyb.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/company/{company_id}/kyb` | 95 | Depends(require_company_admin) | — | N | N |
| POST | `/api/company/{company_id}/kyb/submit` | 143 | Depends(require_company_admin) | — | N | N |
| POST | `/api/company/{company_id}/kyb/upload-url` | 113 | Depends(require_company_admin) | — | N | N |

### `backend/routes/corporate_rider.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/v1/rider/work-profile` | 103 | Depends(get_current_user) | — | N | N |
| POST | `/api/v1/rider/work-profile/accept-invite` | 133 | Depends(get_current_user) | — | N | N |
| GET | `/api/v1/rider/work-profile/auto-match` | 125 | Depends(get_current_user) | — | N | N |
| POST | `/api/v1/rider/work-profile/join-domain` | 147 | Depends(get_current_user) | — | N | N |
| GET | `/api/v1/rider/work-profile/{company_id}/allowance-requests` | 425 | Depends(get_current_user) | — | N | Y |
| POST | `/api/v1/rider/work-profile/{company_id}/allowance-requests` | 369 | Depends(get_current_user) | — | N | Y |
| GET | `/api/v1/rider/work-profile/{company_id}/balance` | 191 | Depends(get_current_user) | — | N | Y |
| GET | `/api/v1/rider/work-profile/{company_id}/rides` | 211 | Depends(get_current_user) | — | N | Y |
| GET | `/api/v1/rider/work-profile/{company_id}/statement/{month}` | 307 | Depends(get_current_user) | — | N | Y |
| GET | `/api/v1/rider/work-profile/{company_id}/statement/{month}/pdf` | 325 | Depends(get_current_user) | — | N | Y |

### `backend/routes/corporate_signup.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/portal/companies/signup` | 64 | Depends(get_current_user) | @limiter.limit("3/hour") | N | Y |

### `backend/routes/corporate_subscriptions.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/admin/corporate-accounts/subscription-plans` | 87 | [Depends(require_module('corporate_accounts'))]; Depends(get_admin_user) | — | N | N |
| GET | `/admin/corporate-accounts/{company_id}/subscription` | 93 | [Depends(require_module('corporate_accounts'))]; Depends(get_admin_user) | — | N | Y |
| POST | `/admin/corporate-accounts/{company_id}/subscription` | 103 | [Depends(require_module('corporate_accounts'))]; Depends(get_admin_user) | — | N | Y |
| POST | `/admin/corporate-accounts/{company_id}/subscription-pilot` | 150 | [Depends(require_module('corporate_accounts'))]; Depends(get_admin_user) | — | N | Y |
| POST | `/admin/corporate-accounts/{company_id}/subscription/cancel` | 134 | [Depends(require_module('corporate_accounts'))]; Depends(get_admin_user) | — | N | N |

### `backend/routes/corporate_wallet.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/admin/corporate-accounts/wallet-portfolio` | 106 | [Depends(require_module('corporate_accounts'))]; Depends(get_admin_user) | — | N | Y |
| GET | `/admin/corporate-accounts/{company_id}/wallet` | 126 | [Depends(require_module('corporate_accounts'))]; Depends(get_admin_user) | — | Y | Y |
| POST | `/admin/corporate-accounts/{company_id}/wallet/adjust` | 262 | [Depends(require_module('corporate_accounts'))]; Depends(get_admin_user) | — | Y | Y |
| PUT | `/admin/corporate-accounts/{company_id}/wallet/config` | 327 | [Depends(require_module('corporate_accounts'))]; Depends(get_admin_user) | — | N | Y |
| POST | `/admin/corporate-accounts/{company_id}/wallet/topup` | 159 | [Depends(require_module('corporate_accounts'))]; Depends(get_admin_user) | — | Y | Y |

### `backend/routes/disputes.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/admin/disputes` | 157 | Depends(require_module("disputes"))  [admin/__init__.py:339]; Depends(get_current_admin) | — | N | Y |
| PUT | `/api/admin/disputes/{dispute_id}/resolve` | 206 | Depends(require_module("disputes"))  [admin/__init__.py:339]; Depends(get_current_admin) | — | Y | Y |
| GET | `/disputes` | 128 | Depends(get_current_user) | — | N | N |
| POST | `/disputes` | 55 | Depends(get_current_user) | — | N | Y |
| GET | `/disputes/{dispute_id}` | 141 | Depends(get_current_user) | — | N | N |

### `backend/routes/drivers/appeals.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/v1/drivers/appeals` | 43 | Depends(get_current_user) | — | N | N |
| POST | `/api/v1/drivers/appeals` | 50 | Depends(get_current_user) | — | N | N |

### `backend/routes/drivers/crc_consent.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/v1/drivers/crc-consent` | 30 | Depends(get_current_user) | — | N | N |
| POST | `/api/v1/drivers/crc-consent` | 49 | Depends(get_current_user) | — | Y | N |

### `backend/routes/drivers/earnings.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/v1/drivers/balance` | 47 | Depends(get_current_user) | — | N | Y |
| GET | `/api/v1/drivers/bonuses` | 326 | Depends(get_current_user) | — | N | Y |
| GET | `/api/v1/drivers/earnings` | 364 | Depends(get_current_user) | — | N | Y |
| GET | `/api/v1/drivers/earnings/comparison` | 900 | Depends(get_current_user) | — | N | Y |
| GET | `/api/v1/drivers/earnings/daily` | 547 | Depends(get_current_user) | — | N | Y |
| GET | `/api/v1/drivers/earnings/forecast` | 987 | Depends(get_current_user) | — | N | Y |
| GET | `/api/v1/drivers/earnings/monthly` | 798 | Depends(get_current_user) | — | N | Y |
| GET | `/api/v1/drivers/earnings/trips` | 609 | Depends(get_current_user) | — | N | Y |
| GET | `/api/v1/drivers/earnings/weekly` | 678 | Depends(get_current_user) | — | N | N |

### `backend/routes/drivers/location.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/v1/drivers` | 864 | Depends(get_admin_user) | — | N | N |
| POST | `/api/v1/drivers` | 886 | Depends(get_admin_user) | — | N | N |
| POST | `/api/v1/drivers/attest-device` | 1353 | Depends(get_current_user) | — | N | N |
| POST | `/api/v1/drivers/attest-nonce` | 1340 | Depends(get_current_user) | — | N | N |
| POST | `/api/v1/drivers/location-batch` | 1056 | Depends(get_current_user), Depends(get_token_session_id) | @location_update_limit | N | N |
| POST | `/api/v1/drivers/location-live` | 973 | Depends(get_current_user), Depends(get_token_session_id) | @location_update_limit | N | N |
| GET | `/api/v1/drivers/nearby` | 716 | Depends(get_current_user) | — | N | N |

### `backend/routes/drivers/offer_receipts.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/v1/drivers/offers/{offer_id}/receipts` | 90 | Depends(get_current_user), Depends(get_token_session_id) | @ride_read_limit | N | N |

### `backend/routes/drivers/payouts.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| DELETE | `/api/v1/drivers/bank-account` | 744 | Depends(get_current_user) | — | N | Y |
| GET | `/api/v1/drivers/bank-account` | 102 | Depends(get_current_user) | — | N | Y |
| POST | `/api/v1/drivers/bank-account` | 712 | Depends(get_current_user) | — | N | Y |
| GET | `/api/v1/drivers/payouts` | 1358 | Depends(get_current_user) | — | N | Y |
| POST | `/api/v1/drivers/payouts` | 849 | Depends(get_current_user) | — | N | Y |
| POST | `/api/v1/drivers/payouts/_legacy` | 901 | Depends(get_current_user) | — | Y | Y |
| POST | `/api/v1/drivers/payouts/instant` | 1081 | Depends(get_current_user) | — | Y | Y |
| GET | `/api/v1/drivers/payouts/instant/quote` | 1337 | Depends(get_current_user) | — | N | Y |
| POST | `/api/v1/drivers/stripe-account-session` | 526 | Depends(get_current_user) | — | Y | Y |
| GET | `/api/v1/drivers/stripe-embedded` | 698 | **NONE FOUND** | — | N | Y |
| POST | `/api/v1/drivers/stripe-onboard` | 329 | Depends(get_current_user) | — | N | Y |
| GET | `/api/v1/drivers/stripe-refresh` | 513 | **NONE FOUND** | — | N | Y |
| GET | `/api/v1/drivers/stripe-return` | 498 | **NONE FOUND** | — | N | Y |
| POST | `/api/v1/drivers/stripe-sync` | 413 | Depends(get_current_user) | — | N | Y |
| GET | `/api/v1/drivers/stripe/bank-payouts` | 1409 | Depends(get_current_user) | — | N | Y |
| GET | `/api/v1/drivers/stripe/ledger` | 1455 | Depends(get_current_user) | — | N | Y |

### `backend/routes/drivers/profile.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/v1/drivers/config` | 58 | Depends(get_current_user) | — | N | N |
| GET | `/api/v1/drivers/demand-heatmap` | 471 | Depends(get_current_user) | @heatmap_read_limit | N | Y |
| DELETE | `/api/v1/drivers/destination` | 1060 | Depends(get_current_user) | — | N | N |
| GET | `/api/v1/drivers/destination` | 1083 | Depends(get_current_user) | — | N | N |
| POST | `/api/v1/drivers/destination` | 1024 | Depends(get_current_user) | — | N | N |
| GET | `/api/v1/drivers/me` | 104 | Depends(get_current_user) | — | N | N |
| PUT | `/api/v1/drivers/me` | 157 | Depends(get_current_user) | — | N | Y |
| POST | `/api/v1/drivers/register` | 828 | Depends(get_current_user) | — | N | N |

### `backend/routes/drivers/referrals.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/v1/drivers/leaderboard` | 341 | Depends(get_current_user) | — | N | N |
| GET | `/api/v1/drivers/referral` | 90 | Depends(get_current_user) | — | N | Y |
| POST | `/api/v1/drivers/referral/apply` | 207 | Depends(get_current_user) | — | N | Y |
| GET | `/api/v1/drivers/referrals` | 274 | Depends(get_current_user) | — | N | Y |

### `backend/routes/drivers/ride_cancel.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/v1/drivers/rides/{ride_id}/cancel` | 40 | Depends(get_current_user) | — | N | N |
| POST | `/api/v1/drivers/rides/{ride_id}/noshow` | 491 | Depends(get_current_user) | — | Y | Y |
| POST | `/api/v1/drivers/rides/{ride_id}/rate-rider` | 846 | Depends(get_current_user) | — | N | N |

### `backend/routes/drivers/ride_complete.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/v1/drivers/rides/{ride_id}/complete` | 397 | Depends(get_current_user), Depends(get_token_session_id) | — | N | N |

### `backend/routes/drivers/ride_flow.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/v1/drivers/rides/{ride_id}/accept` | 79 | Depends(get_current_user), Depends(get_token_session_id) | — | Y | N |
| POST | `/api/v1/drivers/rides/{ride_id}/arrive` | 1032 | Depends(get_current_user) | — | N | Y |
| POST | `/api/v1/drivers/rides/{ride_id}/decline` | 653 | Depends(get_current_user), Depends(get_token_session_id) | — | N | N |
| POST | `/api/v1/drivers/rides/{ride_id}/start` | 1263 | Depends(get_current_user) | — | N | N |
| POST | `/api/v1/drivers/rides/{ride_id}/verify-otp` | 1164 | Depends(get_current_user) | — | N | N |

### `backend/routes/drivers/ride_reads.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/v1/drivers/rides/active` | 57 | Depends(get_current_user) | @ride_read_limit | N | N |
| GET | `/api/v1/drivers/rides/history` | 550 | Depends(get_current_user) | @ride_read_limit | N | N |
| GET | `/api/v1/drivers/rides/upcoming` | 500 | Depends(get_current_user) | @ride_read_limit | N | N |
| GET | `/api/v1/drivers/rides/{ride_id}/offer` | 274 | Depends(get_current_user) | @ride_read_limit | N | N |

### `backend/routes/drivers/status.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/v1/drivers/me/availability` | 222 | Depends(get_current_user), Depends(get_token_session_id) | — | Y | N |
| POST | `/api/v1/drivers/me/availability` | 261 | Depends(get_current_user), Depends(get_token_session_id) | — | N | N |
| GET | `/api/v1/drivers/{driver_id}` | 303 | Depends(get_current_user) | — | N | N |
| PUT | `/api/v1/drivers/{driver_id}/status` | 370 | Depends(get_current_user), Depends(get_token_session_id) | — | N | N |

### `backend/routes/drivers/subscriptions.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/v1/drivers/subscription/cancel` | 1565 | Depends(get_current_user) | — | N | Y |
| GET | `/api/v1/drivers/subscription/checkout-return` | 271 | **NONE FOUND** | — | N | Y |
| GET | `/api/v1/drivers/subscription/current` | 87 | Depends(get_current_user) | — | N | N |
| GET | `/api/v1/drivers/subscription/payments` | 1413 | Depends(get_current_user) | — | N | Y |
| POST | `/api/v1/drivers/subscription/payments/{payment_id}/resend-invoice` | 1490 | Depends(get_current_user) | — | N | Y |
| GET | `/api/v1/drivers/subscription/plans` | 39 | Depends(get_current_user) | — | N | N |
| POST | `/api/v1/drivers/subscription/subscribe` | 320 | Depends(get_current_user) | — | N | Y |
| GET | `/api/v1/drivers/subscription/verify-session` | 786 | Depends(get_current_user) | — | Y | Y |

### `backend/routes/drivers/tax_exports.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/v1/drivers/earnings/export` | 291 | Depends(get_current_user) | — | N | N |
| POST | `/api/v1/drivers/earnings/export/email` | 450 | Depends(get_current_user) | @tax_doc_email_limit | N | Y |
| POST | `/api/v1/drivers/me/export-data` | 883 | Depends(get_current_user) | @dsar_export_limit | N | Y |
| POST | `/api/v1/drivers/statements/email` | 505 | Depends(get_current_user) | @tax_doc_email_limit | N | Y |
| GET | `/api/v1/drivers/t4a/years` | 57 | Depends(get_current_user) | — | N | Y |
| GET | `/api/v1/drivers/t4a/{year}` | 168 | Depends(get_current_user) | — | N | Y |
| POST | `/api/v1/drivers/t4a/{year}/email` | 427 | Depends(get_current_user) | @tax_doc_email_limit | N | N |
| GET | `/api/v1/drivers/t4a/{year}/pdf` | 276 | Depends(get_current_user) | — | N | N |

### `backend/routes/fares.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/fares` | 359 | **NONE FOUND** | — | N | Y |
| GET | `/vehicle-types` | 79 | **NONE FOUND** | — | N | Y |

### `backend/routes/favorites.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/favorites` | 38 | Depends(get_current_user) | — | N | N |
| POST | `/favorites` | 59 | Depends(get_current_user) | — | N | N |
| POST | `/favorites/from-ride/{ride_id}` | 143 | Depends(get_current_user) | — | N | N |
| DELETE | `/favorites/{favorite_id}` | 132 | Depends(get_current_user) | — | N | N |
| POST | `/favorites/{favorite_id}/use` | 115 | Depends(get_current_user) | — | N | N |

### `backend/routes/legacy_consent.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/consent/accept` | 86 | Depends(get_current_user) | — | N | N |
| GET | `/consent/status` | 70 | Depends(get_current_user) | — | N | N |

### `backend/routes/legal_documents.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/legal-documents` | 47 | **NONE FOUND** | — | N | N |

### `backend/routes/lost_and_found.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/lost-and-found` | 173 | Depends(get_current_user) | — | N | N |
| POST | `/lost-and-found/driver-report` | 253 | Depends(get_current_user) | — | N | N |
| GET | `/lost-and-found/{case_id}` | 235 | Depends(get_current_user) | — | N | N |
| GET | `/lost-and-found/{case_id}/messages` | 401 | Depends(get_current_user) | — | N | N |
| POST | `/lost-and-found/{case_id}/messages` | 430 | Depends(get_current_user) | — | N | N |
| POST | `/lost-and-found/{case_id}/messages/image` | 468 | Depends(get_current_user) | — | N | N |
| PUT | `/lost-and-found/{case_id}/respond` | 344 | Depends(get_current_user) | — | N | N |

### `backend/routes/loyalty.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/loyalty` | 79 | Depends(get_current_user) | — | N | N |
| POST | `/loyalty/earn` | 121 | Depends(get_current_user) | — | Y | Y |
| GET | `/loyalty/history` | 100 | Depends(get_current_user) | — | N | N |
| POST | `/loyalty/redeem` | 215 | Depends(get_current_user) | — | N | Y |

### `backend/routes/maps_proxy.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/maps/directions` | 316 | Depends(get_current_user) | @limiter.limit("60/minute", key_func=get_user_or_ip_key) | N | N |
| GET | `/maps/pickup-points` | 435 | Depends(get_current_user) | @limiter.limit("120/minute") | N | N |
| GET | `/maps/places/autocomplete` | 137 | Depends(get_current_user) | @limiter.limit("120/minute") | N | N |
| GET | `/maps/places/details` | 205 | Depends(get_current_user) | @limiter.limit("120/minute") | N | Y |
| GET | `/maps/reverse-geocode` | 240 | Depends(get_current_user) | @limiter.limit("120/minute") | N | N |

### `backend/routes/marketing.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/marketing/preferences` | 131 | Depends(get_current_user) | — | N | N |
| PUT | `/marketing/preferences` | 143 | Depends(get_current_user) | — | N | N |
| GET | `/marketing/unsubscribe` | 106 | **NONE FOUND** | @limiter.limit("60/minute") | N | N |
| POST | `/marketing/unsubscribe` | 95 | **NONE FOUND** | @limiter.limit("60/minute") | N | N |

### `backend/routes/notifications.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| DELETE | `/notifications` | 633 | Depends(get_current_user) | — | N | Y |
| GET | `/notifications` | 520 | Depends(get_current_user) | — | N | N |
| POST | `/notifications/debug-ride-offer` | 180 | Depends(get_admin_user) | — | N | N |
| GET | `/notifications/preferences` | 699 | Depends(get_current_user) | — | N | N |
| PUT | `/notifications/preferences` | 722 | Depends(get_current_user) | — | N | Y |
| PUT | `/notifications/read-all` | 576 | Depends(get_current_user) | — | N | Y |
| POST | `/notifications/register-token` | 360 | Depends(get_current_user) | — | N | N |
| POST | `/notifications/test-push` | 82 | Depends(get_admin_user) | — | N | N |
| DELETE | `/notifications/{notification_id}` | 609 | Depends(get_current_user) | — | N | N |
| PUT | `/notifications/{notification_id}/read` | 565 | Depends(get_current_user) | — | N | N |

### `backend/routes/offer_card.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/offer-cards/{ride_id}.png` | 57 | **NONE FOUND** | @default_limiter.limit("60/minute") | N | Y |

### `backend/routes/payments.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/payments/cards` | 873 | Depends(get_current_user) | @payment_action_limit | N | Y |
| POST | `/payments/cards` | 983 | Depends(get_current_user) | @payment_action_limit | N | Y |
| DELETE | `/payments/cards/{card_id}` | 1210 | Depends(get_current_user) | @payment_action_limit | N | Y |
| POST | `/payments/cards/{card_id}/default` | 1145 | Depends(get_current_user) | @payment_action_limit | N | Y |
| POST | `/payments/confirm` | 587 | Depends(get_current_user) | @payment_action_limit | Y | Y |
| POST | `/payments/create-intent` | 421 | Depends(get_current_user) | @payment_action_limit | Y | Y |
| GET | `/payments/methods` | 821 | Depends(get_current_user) | @payment_action_limit | N | Y |
| POST | `/payments/payment-sheet` | 1298 | Depends(get_current_user) | @payment_action_limit | Y | Y |
| POST | `/payments/setup-intent` | 778 | Depends(get_current_user) | @payment_action_limit | Y | Y |

### `backend/routes/promotions.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/promo/apply` | 406 | Depends(get_current_user) | — | N | Y |
| GET | `/promo/available` | 652 | Depends(get_current_user) | @promo_available_limit | N | Y |
| POST | `/promo/validate` | 389 | Depends(get_current_user) | @promo_validate_limit | N | Y |

### `backend/routes/quests.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/admin/quests/create` | 427 | [Depends(require_module('promotions'))]; Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/quests/list` | 453 | [Depends(require_module('promotions'))]; Depends(get_admin_user) | — | N | N |
| PATCH | `/api/admin/quests/{quest_id}` | 506 | [Depends(require_module('promotions'))]; Depends(get_admin_user) | — | N | N |
| GET | `/api/admin/quests/{quest_id}/participants` | 530 | [Depends(require_module('promotions'))]; Depends(get_admin_user) | — | N | N |
| GET | `/quests` | 102 | Depends(get_current_user) | — | N | Y |
| GET | `/quests/my-quests` | 257 | Depends(get_current_user) | — | N | Y |
| POST | `/quests/progress/{progress_id}/claim` | 311 | Depends(get_current_user) | — | Y | Y |
| POST | `/quests/{quest_id}/join` | 189 | Depends(get_current_user) | — | N | N |

### `backend/routes/rides/booking.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/v1/rides` | 380 | Depends(get_current_user) | @ride_request_limit | N | Y |

### `backend/routes/rides/cancellation.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| DELETE | `/api/v1/rides/scheduled/{ride_id}` | 956 | Depends(get_current_user) | @cancel_ride_limit | N | Y |
| POST | `/api/v1/rides/{ride_id}/cancel` | 42 | Depends(get_current_user) | @cancel_ride_limit | N | Y |

### `backend/routes/rides/chat.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/v1/rides/{ride_id}/chat-status` | 27 | Depends(get_current_user) | — | N | N |
| GET | `/api/v1/rides/{ride_id}/messages` | 69 | Depends(get_current_user) | — | N | N |
| POST | `/api/v1/rides/{ride_id}/messages` | 103 | Depends(get_current_user) | @ride_message_limit | N | N |
| POST | `/api/v1/rides/{ride_id}/typing` | 209 | Depends(get_current_user) | — | N | N |

### `backend/routes/rides/estimates.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/v1/rides/estimate` | 672 | Depends(get_current_user) | @api_rate_limit | N | N |

### `backend/routes/rides/lifecycle.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/v1/rides/{ride_id}/complete` | 170 | Depends(get_current_user) | @ride_action_limit | N | Y |
| POST | `/api/v1/rides/{ride_id}/simulate-arrival` | 42 | Depends(get_current_user) | @api_rate_limit | N | N |
| POST | `/api/v1/rides/{ride_id}/start` | 70 | Depends(get_current_user) | @ride_action_limit | N | N |

### `backend/routes/rides/lost_found.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/v1/rides/{ride_id}/lost-and-found` | 32 | Depends(get_current_user) | @ride_action_limit | N | N |

### `backend/routes/rides/payments.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/v1/rides/{ride_id}/process-payment` | 374 | Depends(get_current_user) | @payment_action_limit | Y | Y |
| POST | `/api/v1/rides/{ride_id}/tip` | 123 | Depends(get_current_user) | @payment_action_limit | N | Y |

### `backend/routes/rides/queries.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/v1/rides/active` | 46 | Depends(get_current_user) | @ride_read_limit | N | Y |
| GET | `/api/v1/rides/history` | 157 | Depends(get_current_user) | @ride_read_limit | N | Y |
| GET | `/api/v1/rides/scheduled` | 304 | Depends(get_current_user) | @ride_read_limit | N | N |
| GET | `/api/v1/rides/stats` | 232 | Depends(get_current_user) | — | N | N |
| GET | `/api/v1/rides/{ride_id}` | 351 | Depends(get_current_user) | @ride_read_limit | N | N |

### `backend/routes/rides/rating.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/v1/rides/{ride_id}/rate` | 43 | Depends(get_current_user) | @ride_rating_limit | Y | Y |

### `backend/routes/rides/receipts.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/v1/rides/{ride_id}/email-receipt` | 274 | Depends(get_current_user) | @ride_action_limit | N | N |
| GET | `/api/v1/rides/{ride_id}/receipt` | 42 | Depends(get_current_user) | — | N | Y |
| GET | `/api/v1/rides/{ride_id}/receipt.pdf` | 202 | Depends(get_current_user) | @ride_read_limit | N | Y |

### `backend/routes/rides/safety.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/api/v1/rides/emergency` | 589 | Depends(get_current_user_allow_expired) | @ride_action_limit | Y | N |
| POST | `/api/v1/rides/{ride_id}/emergency` | 92 | Depends(get_current_user_allow_expired) | @ride_action_limit | Y | Y |
| POST | `/api/v1/rides/{ride_id}/emergency/false-alarm` | 483 | Depends(get_current_user) | — | N | N |
| POST | `/api/v1/rides/{ride_id}/safety-checkin` | 912 | Depends(get_current_user) | @ride_action_limit | N | N |

### `backend/routes/rides/sharing.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/v1/rides/track/{share_token}` | 229 | **NONE FOUND** | @share_track_limit | N | N |
| POST | `/api/v1/rides/{ride_id}/live-activity/register` | 331 | Depends(get_current_user) | @ride_action_limit | N | N |
| GET | `/api/v1/rides/{ride_id}/share` | 42 | Depends(get_current_user) | — | N | N |
| POST | `/api/v1/rides/{ride_id}/share` | 127 | Depends(get_current_user) | @api_rate_limit | N | N |
| GET | `/api/v1/rides/{ride_id}/shared-contacts` | 218 | Depends(get_current_user) | — | N | N |

### `backend/routes/rides/stops.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| PATCH | `/api/v1/rides/{ride_id}/notes` | 258 | Depends(get_current_user) | @ride_action_limit | N | N |
| POST | `/api/v1/rides/{ride_id}/stops` | 72 | Depends(get_current_user) | @ride_action_limit | N | Y |
| DELETE | `/api/v1/rides/{ride_id}/stops/{stop_index}` | 136 | Depends(get_current_user) | @ride_action_limit | N | Y |
| POST | `/api/v1/rides/{ride_id}/stops/{stop_index}/complete` | 196 | Depends(get_current_user) | @ride_action_limit | N | N |

### `backend/routes/rides/tracking.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/v1/rides/{ride_id}/live-route` | 69 | Depends(get_current_user) | — | N | N |
| GET | `/api/v1/rides/{ride_id}/navigation-steps` | 206 | Depends(get_current_user) | — | N | N |

### `backend/routes/safety.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/safety/report` | 62 | Depends(get_current_user) | — | N | N |
| POST | `/safety/report/{incident_id}/photo` | 150 | Depends(get_current_user) | — | N | N |

### `backend/routes/service_areas.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/service-areas` | 73 | **NONE FOUND** | — | N | N |
| GET | `/service-areas/{area_id}/airport-zones` | 109 | **NONE FOUND** | — | N | N |

### `backend/routes/settings.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/api/v1/company-info` | 136 | **NONE FOUND** | — | N | N |
| GET | `/api/v1/settings` | 56 | **NONE FOUND** | — | N | Y |
| GET | `/api/v1/settings/legal` | 117 | **NONE FOUND** | — | N | N |

### `backend/routes/support.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/support/chat` | 71 | Depends(get_current_user_active_session) | @ai_chat_limit | N | N |
| POST | `/support/escalate` | 118 | Depends(get_current_user) | — | N | N |

### `backend/routes/users.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| DELETE | `/users/account` | 451 | Depends(get_current_user) | — | N | Y |
| POST | `/users/data-export` | 242 | Depends(get_current_user) | @dsar_export_limit | N | Y |
| GET | `/users/emergency-contacts` | 840 | Depends(get_current_user) | — | N | N |
| POST | `/users/emergency-contacts` | 867 | Depends(get_current_user) | — | N | N |
| DELETE | `/users/emergency-contacts/{contact_id}` | 944 | Depends(get_current_user) | — | Y | Y |
| DELETE | `/users/profile` | 531 | Depends(get_current_user) | — | N | N |
| GET | `/users/profile` | 94 | Depends(get_current_user) | — | N | N |
| POST | `/users/profile` | 103 | Depends(get_current_user) | — | N | N |
| PUT | `/users/profile-image` | 671 | Depends(get_current_user) | — | N | N |
| PATCH | `/users/profile/corporate` | 718 | Depends(get_current_user) | — | N | N |
| PATCH | `/users/profile/phone` | 574 | Depends(get_current_user) | — | N | N |
| GET | `/users/referral` | 1082 | Depends(get_current_user) | — | N | N |
| POST | `/users/referral/apply` | 1100 | Depends(get_current_user) | — | N | Y |
| GET | `/users/referrals` | 1091 | Depends(get_current_user) | — | N | N |
| POST | `/users/verify-email/confirm` | 1310 | Depends(get_current_user) | — | N | N |
| POST | `/users/verify-email/request` | 1202 | Depends(get_current_user) | @rider_email_verify_request_limit | N | N |

### `backend/routes/wallet.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| GET | `/wallet` | 125 | Depends(get_current_user) | — | N | Y |
| POST | `/wallet/pay` | 226 | Depends(get_current_user) | — | Y | Y |
| POST | `/wallet/top-up` | 137 | Depends(get_current_user) | — | Y | Y |
| GET | `/wallet/transactions` | 340 | Depends(get_current_user) | — | N | Y |

### `backend/routes/webhooks.py`

| Method | Path | Line | Auth dependency | Rate-limited | Idemp (heur.) | Money (heur.) |
|---|---|---|---|---|---|---|
| POST | `/webhooks/ses` | 2430 | **NONE FOUND** | @default_limiter.limit("100/minute") | N | N |
| POST | `/webhooks/stripe` | 659 | **NONE FOUND** | — | Y | Y |
| POST | `/webhooks/twilio-inbound` | 2536 | **NONE FOUND** | @default_limiter.limit("60/minute") | N | N |

## §5 Cross-references (not re-derived)

- SEC-R10-011 (`security.md`): 55 of 102 *id-scoped* routes use a "fetch then compare" ownership pattern with no lint enforcing it — a narrower, deeper claim than this file's auth-dependency sweep (SEC-R10-011 is about *object-level* authorization within an authenticated route, this file is about whether a route has *any* authentication dependency at all). The two are complementary, not duplicative.
- SEC-R10-012 (`security.md`): dev-agent permissions on `git push origin main`, not an API-route finding — cross-referenced in `agent-permission.md` (this Tier-B build), not here.
- `docs/audit/clean-sheet/02-findings/dispatch.md`, `reliability.md`: dispatch-specific route behavior (offer claim race, matching endpoints) — this file lists the routes; those lanes assess them in depth.

## §6 What was not checked

- No live server boot / OpenAPI schema dump — this is 100% static AST+regex analysis; a route registered dynamically (not via a literal `@router.<method>("...")` decorator) would be invisible to it. Spot-checked: no dynamic route registration found (`add_api_route(` grep → 0 hits outside FastAPI's own internals).
- Rate-limit *values* (the actual requests/window) are not extracted, only decorator presence — see `security.md`/`dispatch.md` for specific limiter configs already documented there.
- The `/mcp` ASGI mount (`ai/mcp_server.py`) is a separate protocol surface, not FastAPI routes, and is out of scope for this file — `security.md` §1.9 already covers its permission model.
- Auth-dependency **correctness** (does `get_admin_user` actually enforce what it claims) is not re-verified here; `security.md` §1.1-§1.2 already did that deep read.
- §2's "legitimate by design" calls are this session's judgment on a single code read, not a second-reviewer pass — flagged INFERRED, not VERIFIED-by-two-readers.

