# Admin Portal Security + RBAC Audit — 2026-09-10

**Scope:** `admin-dashboard/` (frontend) + `backend/routes/admin/**` + shared auth layer
(`backend/dependencies/__init__.py`, `backend/core/config.py`, `backend/utils/refresh_tokens.py`)
+ `backend/features.py` (pulled in because the admin-dashboard calls it).

**Method:** Two parallel specialized subagent audits (`spinr-security-auditor` — general OWASP-lens
pass; `spinr-admin-rbac-reviewer` — module-grant workflow deep pass), then direct manual
verification of both BLOCKER-severity claims against source (grep + read, not re-run of the
subagents' own output). WARNING/INFO findings below carry the subagents' own file:line citations
but were not independently re-verified line-by-line — flagged explicitly in "What was NOT verified."

**Overall posture:** Strong baseline — blanket `Depends(get_admin_user)` + explicit
`require_module`/`require_super_admin` on effectively every admin router, SHA-256 rotated refresh
tokens with reuse-detection cascade, bcrypt admin password with a weak-value blocklist including
`admin123`, CSP with no `unsafe-inline`/`unsafe-eval` in prod, no frontend-only RBAC gating found
anywhere (every UI hide has a matching backend check), no typo'd/unreachable module strings. The
findings below are real gaps found despite that baseline.

---

## BLOCKERS (2) — verified against source, fix before this surface sees more live use

### B1 — SIN/DOB backfill endpoint missing super_admin gate
**File:** `backend/routes/admin/legacy_sin_dob_backfill.py`, mounted
`backend/routes/admin/__init__.py:178`

```python
admin_router.include_router(legacy_sin_dob_backfill_router, dependencies=[Depends(require_module("drivers"))])
```
Confirmed by direct grep: **zero** occurrences of `super_admin`/`require_role`/`require_module` inside
`legacy_sin_dob_backfill.py` itself — the file relies entirely on the mount's `require_module("drivers")`.

`POST /api/admin/legacy-drivers/sin-dob-backfill/commit` writes a real, vault-encrypted **SIN + date
of birth** onto driver rows. Every comparable SIN-writing endpoint in this codebase requires
`super_admin`, confirmed:
- `backend/routes/admin/drivers.py` `reveal-sin` / `update-sin` — inline `role != "super_admin"` check
- `backend/routes/admin/tax_id_import.py`, mounted `backend/routes/admin/__init__.py:274`:
  `dependencies=[Depends(require_super_admin)]`

**Exploit:** any staff account holding the ordinary `"drivers"` module grant (e.g. the `operations`
preset — not super_admin) can commit SINs today.

**Fix:** mount with `dependencies=[Depends(require_super_admin)]` to match `tax_id_import_router`,
plus an inline `_require_super_admin(admin)` check in the handler as defense-in-depth (this repo's
established pattern for every other super_admin-class router).

**Blast radius:** isolated to `drivers` table SIN/DOB fields; no schema change.

---

### B2 — Unscoped shadow endpoints for fare/dispatch mutations (`pricing_router`)
**File:** `backend/features.py:466-639`, mounted `backend/server.py:485`
(`v1_api_router.include_router(pricing_router)` — no additional dependency at mount time)

```python
pricing_router = APIRouter(tags=["Pricing"], dependencies=[Depends(get_admin_user)])
```
Confirmed by direct read: `create_area_fee` (:509), `update_area_fee` (:537), `delete_area_fee` (:553),
and `assign_driver_area` (:630) are gated **only** by "holds any valid admin JWT" — no
`require_module`, and none of these four handlers call `log_admin_action` (confirmed by reading each
function body). `update_area_tax` (:560) is the one exception — it does call `log_admin_action` at
:584, so that one specific route is audited; the other four are not.

These duplicate UI-wired endpoints that **are** correctly module-scoped:
- `backend/routes/admin/service_areas.py` fee CRUD → gated via
  `backend/routes/admin/__init__.py:162`: `require_module("service_areas")`
- `backend/routes/admin/drivers.py:4110` driver-area assignment → gated via
  `backend/routes/admin/__init__.py:165`: `require_module("drivers")`

**Exploit:** any admin account — including a `support`-only grant with no `service_areas`/`drivers`
module — can call these `/api/v1/areas/{id}/fees` / `/api/v1/drivers/{id}/area` routes directly with
its own valid token to alter live booking fees (changes every subsequent rider fare in that service
area) or silently move a driver out of/into a dispatch pool, with **no audit row** for 4 of the 5
routes.

**Fix:** either delete `pricing_router` if genuinely dead (there's a code comment at :563-567 saying
the `/tax` route specifically "is not currently reachable from any frontend" — worth confirming the
other 4 routes aren't either before deleting), or mount it with the same `require_module(...)` as its
UI-wired twins and add `log_admin_action` to the 4 unaudited handlers.

**Blast radius:** fare calc for every rider in the affected service area
(`services/fare_service.py` reads `area_fees`); dispatch pool membership for the reassigned driver.
No schema change — authz/audit-wiring fix only.

---

## WARNINGS (7) — not verified line-by-line by me; fix or explicitly accept before merge

| # | Finding | File:line | Source |
|---|---|---|---|
| W1 | `stripe_import_router` mount states `require_module("drivers")` but all 5 handlers independently already require `super_admin` — mount is weaker than actual enforcement, one future "trim the redundant check" edit reopens it to any `drivers`-grant admin | `backend/routes/admin/stripe_import.py`, mount `__init__.py:191` | RBAC audit |
| W2 | 7 routers mounted `require_super_admin` with **no per-handler redundant check** (unlike 13 sibling super_admin routers that all double-gate) — single point of failure for PII export/import + SGI government forms + the dual-approval gate | `data_transfer_{export,import,search,jobs}.py`, `sgi_forms.py`, `export_approvals.py`, `migration_status.py` | RBAC audit |
| W3 | `"staff"` module comment says "Only super_admin can access this" but mount uses `require_module("staff")` — a `custom`-role admin holding just that module can read the full staff roster (email/role/modules) via `list_staff`/`get_staff`. Mutations correctly hard-gated separately. | `backend/routes/admin/staff.py:63` vs mount `__init__.py:305` | RBAC audit |
| W4 | Privilege-escalation-by-composition: a super_admin can set `role="custom"` + all 17 `AVAILABLE_MODULES` via `POST/PUT /staff`, reaching super_admin-equivalent access on every `require_module()`-gated router, **without** the `password_confirmation` step actual `role="super_admin"` promotion requires — no distinct audit signal either | `backend/routes/admin/staff.py:182-187, 296-297` | RBAC audit |
| W5 | `change-password` bypasses the shared token-verification helper (`_require_staff_from_token`) — bare `jwt.decode()` with no `is_active`, no JTI revocation-denylist check, no `token_version` check, no TOTP re-check. Defeats `logout-all`'s revocation guarantee and MFA for this one action if an attacker holds a still-unexpired (≤1h) captured token + the account's password | `backend/routes/admin/auth.py:660-738` | Security audit |
| W6 | `/api/auth/set-cookie` (admin-dashboard BFF) accepts any JSON body from any origin with no CSRF/Origin check and writes it into the `admin_token` cookie — unlike every other state-changing BFF route which uses double-submit CSRF. Low impact today (cookie isn't trusted server-side anywhere, verified) but a landmine for any future SSR code that starts trusting it | `admin-dashboard/src/app/api/auth/set-cookie/route.ts:6-29` | Security audit |
| W7 | Sentry `beforeSend` scrubbing (`sentry.scrub.ts`) never touches `event.breadcrumbs` — default Console/Fetch/XHR/click integrations can carry full URLs/query strings and console-log args into Sentry unredacted. No confirmed live violating call site found, structural gap only | `admin-dashboard/sentry.scrub.ts:36-70` | Security audit |

## INFO (3) — low priority, worth tracking

- `GET /admin/auth/session` and `GET /admin/auth/mfa/status` also bare-decode without the JTI/token_version checks (read-only, no mutation capability — cosmetic).
- `zoho-config-card.tsx:247` uses `dangerouslySetInnerHTML` without `DOMPurify` unlike its sibling component; backend currently `html.escape()`s every field so no live XSS, but no independent safety net if a field is added later without escaping.
- Everything under "Verified clean" in both subagent reports (refresh-token handling, `ADMIN_PASSWORD`/`JWT_SECRET` hardening, frontend token storage, CSP, router mount coverage across all 58 admin routers, module-string consistency) — reviewed and no issue found.

---

## What was NOT verified

- I personally re-verified **B1 and B2** (the two blockers) directly against source via grep/read. Everything else — all 7 warnings and 3 info items — is the subagents' own file:line-cited output, not independently re-checked by me line-by-line.
- Nothing here was exercised at runtime — no live request was sent to a running admin-dashboard/backend instance, no actual exploit was executed. These are static-analysis findings.
- No infra-level controls (VPN, WAF, network policy restricting who can reach the admin API at all) were considered — this audit is code-level only.
- No visual/UI check was performed — out of scope for a security/RBAC audit.
