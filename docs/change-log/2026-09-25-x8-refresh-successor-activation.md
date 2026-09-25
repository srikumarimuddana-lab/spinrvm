# Change Impact & Risk Log: make X8 lost-response recovery work (mobile proposals + body-held parent)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (repo owner chose the client-side option and will ship a new rider build) |
| Surface(s) | backend, shared, driver-app, rider-app |
| Domain (Sentry tag) | auth |
| PR / commit link | branch `claude/sleepy-ptolemy-yta2i1` |
| Related issue or gap ID | Follow-up to #5777 / #5780 (unexpected logouts); X8 = `docs/audit/2026-09-24-refresh-successor-commitment-security-note.md` |

## 1. Issue / gap identified

- **Lost renewal reply signs users out.** If a phone loses the server's reply mid-renewal (app killed, network drop), its next renewal replays a token the server already rotated. The server treats the replay as theft and can sign the user out on every device.
- **X8 is inert.** X8 was built to recover exactly this, but it does nothing today.
- **Stale cookie on drivers (likely).** The driver app's foreground renewal may be sending a stale refresh cookie that the server prefers over the fresh token in the body.

## 2. Root cause

- **X8 never commits a successor.** No client sends `proposed_refresh_token`. The server also discards any proposal on a normal renewal: since the 2026-09-24 raw-scope fix, `no_match` clears it. So no successor is ever committed to a proposal, and `recover` can never match.
- **Cookie wins over body.** `refresh_access_token` reads the `refresh_token` cookie before the body. React Native's native HTTP stacks keep cookies from responses and re-send them. As far as I know that is the default, but I have not confirmed it on a device. The driver app's headless background task renews with `credentials: 'omit'`, so the foreground cookie jar keeps the previous token. The next foreground renewal then presents a revoked token, even though the fresh one is in the body.

## 3. Fix / remediation

Server, only when `refresh_successor_commitment_enabled` is on and the body carries a valid proposal:
- **Proposal becomes the successor only on a native-app request** (`_is_native_refresh_request`): no `refresh_token` cookie, no `Origin` or `Referer`, `Sec-Fetch-Site` absent or `none`, and `Content-Type: application/json`. On `no_match` the proposal then becomes the successor's secret. The party that presented the parent is the party receiving the response, so choosing the successor grants nothing new. Cookie absence alone is not enough: the refresh cookies are SameSite=Strict, so a signed-in victim's browser omits them on a cross-site forged POST. Browsers always send `Origin` on a POST, and a cross-site form cannot send JSON.
- **A cookie is never overridden.** Any request with a cookie keeps today's cookie-first parent and a server-random successor. That keeps the 2026-09-24 attack closed and blocks the session-fixation variant found in review (see §9).

Client:
- **New helper.** `shared/auth/refreshProposal.ts`:
  - Makes a 64-character proposal from the platform CSPRNG (`expo-crypto` `getRandomBytes(64)`, `byte & 63`, no bias, 384 bits).
  - Persists it in SecureStore, bound to its parent, before sending.
  - Reuses it for any retry of that parent.
  - Clears it after success and on sign-out.
  - Sends nothing when the RNG output is degenerate, missing, or unsaved, or on web.
- **Callers.** The shared auth store (both apps) and the driver app's background renewal use it. Both run under the session lock and share one pending proposal.
- **No cookies on a proposing request.** A renewal that carries a proposal is sent with `credentials: 'omit'`. The background task already did this; the foreground now does too, through a new opt-in `credentials` option on `shared/api/client.ts` `post` (unchanged for every other caller). This also stops a possibly stale foreground refresh cookie from being presented instead of the token the app holds.
- **Rider dependency.** The rider app gains `expo-crypto`, a native module, so it needs a new rider build.

## 4. Risk & impact on existing functionality

- **Blast radius, server:**
  - `routes/auth.py::refresh_access_token` only.
  - Admin refresh (`routes/admin/auth.py`) has no X8 by design; `test_admin_twin_is_untouched` still pins that.
  - `issue_refresh_token(raw=...)` already validates shape and retries a `23505` collision with a server-random token.
- **Blast radius, client:**
  - `shared/store/authStore.ts` (imported by ~173 files, but only `refreshTokens` and `clearLocalSessionUnlocked` change).
  - `driver-app/utils/backgroundAuth.ts`.
  - The request body gains one optional field. The deployed `RefreshRequest` already declares it, and it is ignored while the flag is off.
- **Tests that assert the exact refresh body.** These are `authStore.refreshRace`, `authStore.initialize` and `backgroundAuth`:
  - `backgroundAuth.test.ts` mocks `expo-crypto` without `getRandomBytes`, so no proposal is generated.
  - The other two do not mock `expo-crypto`. jest-expo's automatic mock returns no bytes or all-zero bytes, and the all-equal guard rejects both.
  - If CI shows otherwise, those assertions need `expect.objectContaining`.
- **Server-side changes are behind the flag:**
  - **Flag off:** cookie precedence and server-random successors are unchanged.
  - **Flag on, old clients:** they send no proposal, so they keep today's path.
- **The flag still needs the X8 audit** in the 2026-09-24 security note before it is enabled. This change is also reviewed below.
- **Recovery edge case:** a client whose SecureStore write of the rotated pair failed will replay the old parent with its pending proposal. With the flag on this now recovers the committed successor; it used to trip reuse detection.
- No interaction with ride state, money, or insurance periods.

## 5. User experience effect

- **Flag off:** none. The field is ignored.
- **Flag on, new builds:**
  - A lost renewal reply no longer signs the user out; the next renewal gets the same new token back.
  - Driver foreground renewals stop failing on a stale cookie.
- There is no visible copy change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/auth.py` | Proposal-as-successor only on a request with no refresh cookie (flag on); cookie never overridden | Make X8 commit successors without a fixation path |
| `backend/tests/test_refresh_successor_route.py` | Cookie-less commit, cookie-parent never commits, forged body can't override a cookie, flag-off unchanged | Pin each rule |
| `shared/api/client.ts` | Opt-in `credentials` on `post` | Let the proposing renewal omit cookies |
| `shared/auth/refreshProposal.ts` | New: generate, persist, reuse, clear proposal | Client half of X8 |
| `shared/store/authStore.ts` | Send the proposal with `credentials: 'omit'`; clear it after success and on sign-out | Foreground renewals (both apps) |
| `driver-app/utils/backgroundAuth.ts` | Send the proposal; clear after success | Headless driver renewals |
| `driver-app/__tests__/auth/refreshProposal.test.ts` | Helper tests | Persist-before-use, reuse, weak-RNG and failure paths |
| `rider-app/package.json`, `rider-app/yarn.lock` | `expo-crypto ~57.0.3` | CSPRNG for the rider app (native build) |

## 7. Before / after

Scenario (flag on, new build): a driver's phone renews with parent P and the reply is lost.

- **Before:**
  1. The phone retries with P.
  2. P is revoked and replaced, so the server runs reuse detection.
  3. The user is signed out, possibly on every device.
- **After:**
  1. The first renewal sent P plus proposal S, which was already saved. The server committed S as P's successor.
  2. The retry sends P plus S again. `classify_committed_replay` returns `recover` and re-serves S.
  3. The user stays signed in.

```python
# Before (no_match)
proposed = None
# After
parent_from_body = bool(proposed and not request.cookies.get("refresh_token"))
...
if not parent_from_body:   # a request with a cookie never chooses its successor
    proposed = None
```

## 8. Rollback plan

- **Server:** `UPDATE public.settings SET refresh_successor_commitment_enabled = false WHERE id = 'app_settings';` or the admin settings API. It takes effect within the 60s settings cache, with no deploy.
- **Client:** proposals are harmless with the flag off, so no app rollback is needed.
- **Already-issued successors:** those committed from proposals are normal refresh rows and keep working.

## 9. Verification performed

- [x] `ruff check` / `ruff format`, `py_compile` on the backend changes.
- [x] TypeScript syntax (`transpileModule`) on all changed or new TS files.
- [ ] pytest and jest: **not run**. This container cannot reach PyPI or npm. CI is the first run.
- [ ] No device or staging test. The "native stacks re-send the refresh cookie" claim is reasoned from React Native's networking defaults, not observed.
- [x] Blast-radius grep for every `/auth/refresh` caller: shared auth store, driver background auth, and a rider `utils/apiClient.ts` cookie-only call that is unchanged.
- [x] `spinr-security-auditor` on the first version: **blocker found and fixed.** That version preferred a body token over a present cookie. A forged browser request (XSS, or a cross-site `text/plain` form, since `/auth/refresh` checks no CSRF token) could then carry the attacker's own refresh token plus a proposal. The attacker's session and chosen successor would be set as cookies in the victim's browser (session fixation). Fixed: the proposal is only committed on a request with no refresh cookie, and a cookie is never overridden. `test_cookie_is_never_overridden_by_a_forged_body_token` pins it. The client helper, RNG and storage were reviewed as sound.
- [x] Re-review of that fix: **second blocker found and fixed.** Cookie absence is what a cross-site forged POST from a signed-in victim looks like (SameSite=Strict withholds the cookie), so it still reached the commit path. The commit now also requires no `Origin`/`Referer`, a `Sec-Fetch-Site` of none or absent, and a JSON `Content-Type`. `test_browser_request_without_cookie_never_uses_the_proposal` covers each signal, and a stdlib harness checked the helper against 10 header combinations.
- [x] Third review of that fix: **passes; the reviewer would not block the flag.** Checked same-origin XSS, cross-site forms, CORS fetch (including allow-listed origins), `no-cors`, `sendBeacon`, service workers, old browsers and WebViews. `Origin`, `Referer` and `Sec-Fetch-Site` cannot be set by script, and no middleware strips them. The native foreground and background paths still qualify. Recommended before enabling the flag: a real-device capture confirming native fetch sends none of those headers. A false negative only disables recovery; it does not open a hole.

## 10. What was NOT verified

- Real-device confirmation that iOS and Android re-send the `refresh_token` cookie, and that `credentials: 'omit'` suppresses it.
- Real-device confirmation that native `fetch` sends no `Origin`, `Referer` or `Sec-Fetch-Site`. If it does, the server fails safe: it never commits the proposal, and renewals behave as today.
- The rider build with `expo-crypto`. The lockfile entry was added by hand from the driver app's identical resolution, because yarn cannot run here. CI's `--frozen-lockfile` install is the check.
- Whether jest-expo's automatic `expo-crypto` mock returns bytes. If it does, three exact-body assertions will need `objectContaining`.
- The X8 audit gate in the 2026-09-24 security note still applies before the flag is enabled.
- **Pre-existing, not introduced here, needs its own fix:** `/auth/refresh` falls back to a body refresh token whenever no cookie arrives and checks no CSRF token. A cross-site form (SameSite=Strict withholds the victim's cookie even when they are signed in) can therefore post the attacker's own refresh token and plant the attacker's session cookies in the victim's browser (login CSRF / session fixation), with a server-random successor. X8 adds nothing to it. Changing that fallback could affect a web build served from a different site than the API, so it is left for a decision. Candidate fix: apply the same native-request check (or require JSON) before using a body token.
