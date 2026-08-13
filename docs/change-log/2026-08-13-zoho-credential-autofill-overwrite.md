# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-13 |
| Author | Claude Code (session: zoho-token-refresh-error) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/zoho-token-refresh-error-xykstq` |
| Related issue or gap ID | Live incident — `POST /api/admin/support-tickets/config/test` → 502 `Zoho token refresh failed: general_error` |

## 1. Issue / gap identified

Saving the Zoho Desk config from the admin dashboard (the admin was toggling the
help-desk **email signature**) silently overwrote the stored Zoho OAuth
`client_id` and `client_secret` with the admin's own email address and password,
supplied by the browser's password manager. Every subsequent Zoho call failed at
the refresh-token grant with Zoho's opaque `general_error`. Toggling the
signature back off did not help, because the corrupted credentials were already
persisted.

Timeline (from `audit_logs`, all UTC 2026-08-13):

| Time | Event |
|---|---|
| 17:08:45 | `zoho_desk_sync` succeeds — 197 tickets upserted. Integration healthy. |
| 19:17:30 | `zoho_desk_config_updated`, `fields_changed` includes `client_id`, `client_secret` |
| 19:20:40 | `zoho_desk_config_updated`, same fields again (the attempted revert) |
| 19:20:46 | `POST /config/test` → 502 `Zoho token refresh failed: general_error` |

Post-incident state of `zoho_desk_config` (shape only — secret values never read):

| Column | Observed | Expected |
|---|---|---|
| `client_id` | 19 chars, contains `@` | `1000.`-prefixed, ~35 chars |
| `client_secret` | 28 chars, contains symbols, not hex | long opaque string, ≥32 chars |
| `refresh_token` | 70 chars, `1000.` prefix — **intact** | `1000.`-prefixed, 70 chars |

Only `admin-001` (`super_admin`) appears in `audit_logs` across the whole window.
No second actor, and no other config-mutating action, was involved.

## 2. Root cause

Two independent defects compounded:

1. **The admin dashboard rendered a browser-recognisable login form.** The
   `ZohoConfigCard` always mounted a plain-text "Client ID" input immediately
   followed by a `type="password"` "Client Secret" input, with no
   `autoComplete` hints. Chromium-family browsers and password managers
   heuristically treat that adjacency as a username/password pair and autofill
   saved credentials into it. The card's save handler sends any secret field
   that is non-empty (`if (clientId.trim()) body.client_id = …`), which cannot
   distinguish a value the admin typed from one the browser injected — so a
   save intended to flip one boolean shipped two overwritten credentials.
   `refresh_token` survived only because autofill fills one password field, not
   both.

2. **The backend accepted them without question.** `PUT /config` guarded only
   against *empty* secret strings ("empty means leave unchanged"). Any non-empty
   value — including an email address — was written straight over a working
   credential. The failure then surfaced one layer away, in
   `services/zoho_desk_service._refresh_access_token`, as an upstream Zoho error
   with no hint that the local config was the problem.

A third, aggravating defect: `PUT /config` invalidated the cached access token
whenever `data_center` was *present in the payload*, not when it *changed*. The
admin UI posts the whole form on every save, so `data_center` is always present
— meaning every unrelated save discarded a valid cached token and forced an
immediate refresh round-trip to Zoho. That is why the breakage was visible
within seconds of the save rather than up to an hour later.

## 3. Fix / remediation

- **admin-dashboard** — the three OAuth credential inputs are no longer rendered
  by default. They mount only after the admin explicitly clicks "Replace
  credentials", so in the normal editing path (signature, org ID, reply-from,
  toggles) there is no autofill target on the page at all. When they are shown,
  they carry `autoComplete="off"`/`"new-password"`, non-credential-looking
  `name` attributes, and `data-1p-ignore` / `data-lpignore` / `data-form-type`
  hints for 1Password, LastPass and Dashlane. Cancel and a successful save both
  clear and re-collapse the editor.
- **backend** — `PUT /config` now validates credential shape before writing and
  returns `400` with an actionable message. Rejected: whitespace in any
  credential, `@` in any credential (the autofill signature), a `client_id` or
  `refresh_token` not prefixed `1000.`, or a `client_secret` under 32
  characters. A rejected save performs no write at all.
- **backend** — the cached access token is now cleared only when a credential or
  `data_center` value actually differs from the stored one.

**Not fixed by this change: the corrupted production row.** The real Client ID
and Client Secret are not recoverable from the database (they were overwritten,
and there is no history table), so an admin must re-enter them from the Zoho API
console. The intact `refresh_token` remains valid provided the re-entered
credentials are the same OAuth client that minted it.

## 4. Risk & impact on existing functionality

Blast radius: **isolated**, verified by grep, not assumed.

- `updateZohoConfig` (`admin-dashboard/src/lib/api/zoho-desk.ts:79`) has exactly
  one caller: `zoho-config-card.tsx:100`.
- `ZohoConfigCard` has exactly one consumer:
  `admin-dashboard/src/app/dashboard/support-tickets/page.tsx:136`.
- `PUT /api/admin/support-tickets/config` has no other client — no mobile
  surface, no background loop, no other admin page writes `zoho_desk_config`.
- `_validate_credentials` is new and called from exactly one place. It cannot
  affect `GET /config`, `POST /config/test`, `POST /sync`, or any ticket route.
- Other readers of `zoho_desk_config` — `services/zoho_desk_service.py`,
  `utils/zoho_desk_sync.py`, and the `zoho_desk_sync` background loop in
  `core/lifespan.py` — only *read* the row (plus the token-cache write inside
  `_refresh_access_token`, which is untouched). The narrower token-invalidation
  condition makes them see a *valid* cached token more often, never a stale one:
  freshness is still enforced independently by `_token_is_fresh`.

No interaction with the ride state machine, dispatch, wallet/allowance deltas,
Stripe, or insurance-period rows. No migration; no schema change.

Residual risk: the `client_secret` minimum length of 32 is inferred from Zoho's
current credential format, not from a published guarantee. If Zoho ever issues a
shorter secret, a legitimate save would be rejected with a clear 400 — a visible,
one-constant fix, and strictly safer than the current silent-overwrite behaviour.

## 5. User-experience effect

Internal admin only (`support_tickets` module). Riders, drivers and corporate
admins see nothing — the customer-facing help-desk email path is unchanged.

- The OAuth credential fields are now behind a "Replace credentials" /
  "Add credentials" button rather than always visible. Existing behaviour is
  preserved: leave a field blank to keep the saved value.
- Pasting a malformed credential now shows a specific error instead of appearing
  to succeed.
- Not visible mid-session to any rider or driver. No notification copy changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/support_tickets.py` | Added `_validate_credentials()`; call it in `update_config`; compare credential/`data_center` values against the stored row before clearing the cached token | Reject autofilled junk at the write; stop discarding good tokens on unrelated saves |
| `backend/tests/test_admin_support_tickets_routes.py` | Added rejection cases (incl. the exact production payload shape), an accept case, and cached-token retain/clear cases | Regression cover for both defects |
| `admin-dashboard/src/app/dashboard/support-tickets/_components/zoho-config-card.tsx` | Credential inputs mount only behind an explicit toggle; added autofill-suppression attributes; centralised input clearing | Remove the autofill target from the default editing path |
| `docs/change-log/2026-08-13-zoho-credential-autofill-overwrite.md` | This entry | Required by `CLAUDE.md` for a live-tested surface |

## 7. Before / after

```python
# Before — routes/admin/support_tickets.py
for secret in ("client_secret", "refresh_token", "client_id"):
    if secret in fields and not (fields[secret] or "").strip():
        fields.pop(secret)
...
# Changing any credential invalidates the cached access token.
if {"client_id", "client_secret", "refresh_token", "data_center"} & set(fields):
    fields["access_token"] = None
    fields["access_token_expires_at"] = None
```

```python
# After
for secret in ("client_secret", "refresh_token", "client_id"):
    if secret in fields and not (fields[secret] or "").strip():
        fields.pop(secret)
...
_validate_credentials(fields)   # 400 on an email / whitespace / wrong prefix / short secret
...
current = await db_supabase.find_one(_CONFIG_TABLE, {"id": _CONFIG_ID}) or {}
if any(
    key in fields and fields[key] != current.get(key)
    for key in ("client_id", "client_secret", "refresh_token", "data_center")
):
    fields["access_token"] = None
    fields["access_token_expires_at"] = None
```

```tsx
// Before — zoho-config-card.tsx: always mounted, no autofill hints
<Input id="zoho-client-id" value={clientId} onChange={...} />
<Input id="zoho-client-secret" type="password" value={clientSecret} onChange={...} />
```

```tsx
// After — mounted only on explicit intent, with autofill suppressed
{editingCredentials && (
  <Input id="zoho-client-id" name="zoho-oauth-client-id" autoComplete="off"
         data-1p-ignore data-lpignore="true" data-form-type="other" ... />
  <Input id="zoho-client-secret" name="zoho-oauth-client-secret" type="password"
         autoComplete="new-password" data-1p-ignore data-lpignore="true"
         data-form-type="other" ... />
)}
```

## 8. Rollback plan

Code-only change, no migration, no live-data mutation — `git revert` is a
complete rollback here, and the credentials are re-enterable from the Zoho API
console either way. Reverting restores the previous (permissive) save path; it
does not resurrect the overwritten credentials, which are unrecoverable
regardless of which version is deployed.

If the new validation blocks a legitimate credential in production before a
redeploy is possible, the value can be written directly to
`zoho_desk_config` with the service-role key, bypassing the route.

## 9. Verification performed

- [x] Automated tests run — `backend/tests/test_admin_support_tickets_routes.py`
      (unit; see the run recorded in the PR/commit). Cases cover the exact
      production payload shape (`client_id` = an email address), whitespace,
      wrong prefix, short secret, the accept path, and both token-invalidation
      branches.
- [x] Blast-radius grep performed — `updateZohoConfig`, `ZohoConfigCard`,
      `zoho_desk_config`, `_config_status`, `update_config` across
      `admin-dashboard/src` and `backend/`.
- [x] Reviewed against `CLAUDE.md` — "do not silently swallow errors" (the
      500-level Zoho failure now has a 400-level cause surfaced at the write),
      admin RBAC (`require_module("support_tickets")` unchanged), audit logging
      (`log_admin_action` unchanged — it already recorded `fields_changed`,
      which is what made this diagnosable).
- [x] Live `audit_logs` and `zoho_desk_config` inspected read-only via the
      Supabase MCP connection to confirm the root cause. Secret *values* were
      never selected — only lengths, prefixes and regex-shape booleans.
- [ ] Manual repro in staging — **not done**, see below.
- [ ] Feature-flagged — **not flagged.** Justification: internal-admin-only
      surface behind an RBAC module, no rider/driver/corporate exposure, and the
      current unflagged behaviour is actively destroying credentials.

## 10. What was NOT verified

- **No production build was run** for `admin-dashboard` (`npm run build`). Node
  deps for that surface were not installed in this environment. The change is
  JSX/prop-level with no new imports or types, but that is reasoning, not a
  build result — run `npm run build` before merge.
- **No browser test of the autofill suppression.** Whether Chrome, Safari,
  1Password, LastPass and Dashlane each honour the attributes was not
  empirically confirmed. The primary defence does not depend on them: the inputs
  are simply not in the DOM in the default path. The attributes are a second
  layer for when the editor is open, and the backend validation is the third.
- **No visual-regression check.** This repo has no snapshot/visual tooling for
  `admin-dashboard`, so the collapsed-credentials layout change was reasoned
  about, not screenshotted. Standing gap — `ACTION_ITEMS.md`.
- **The `client_secret` ≥32 floor was not validated against Zoho's
  documentation**, only against the observed format of Zoho-issued secrets.
- **Nothing was tested against a live Zoho tenant.** Whether the intact
  `refresh_token` still works once the correct `client_id`/`client_secret` are
  restored is unproven until an admin re-enters them and runs "Test connection".
- **The corrupted row was left as-is.** No write was made to
  `zoho_desk_config` from this session; remediation is an operator action.
