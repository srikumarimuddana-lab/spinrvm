# Change Impact & Risk Log — F02 (logout revocation) + F03 (device AI state)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude Code (agent), for the PR #5138 remediation plan |
| Surface(s) | backend, rider-app, shared |
| Domain (Sentry tag) | auth (F02) / ai (F03) |
| PR / commit link | branch `claude/pr-5138-implementation-27zn2l` |
| Related issue or gap ID | F02 + F03, `docs/security/2026-09-08-ai-security-assessment.md` (PR #5138), remediation order 1 |

Two findings in one entry: the assessment groups them as one acceptance
criterion — *"logout blocks retained credentials for that session; another
device remains valid as designed; account B never renders or receives A's
cached/streamed state."* They are the server and client halves of one story.

## 1. Issue / gap identified

**F02 — a signed-out session keeps AI authority.** `/auth/logout` deliberately
leaves the access token valid until its `exp` (15 min default) and writes a
session tombstone. `get_current_user` does not consult that tombstone, so an
offline check confirmed a valid customer JWT still authenticating after logout.
Separately, the shared mobile logout calls `api.post('/auth/logout')` **with no
body**, and the server revokes a refresh token only if one arrives in a cookie
or the body — a probe confirmed logout succeeding with *no* refresh revocation,
leaving a 30-day credential live.

**F03 — rider AI state survives an account switch.** `aiChatStore` used a single
unscoped `spinr_ai_conversation_id` AsyncStorage key and, alone among the
per-session stores, registered **no logout callback**. Messages, conversation
id, pending map pin and stream controller were never reset. The screen renders
the existing store first and loads history asynchronously, so account B could
see account A's messages while the (correct) server-side rejection was still in
flight.

## 2. Root cause

**F02** — two separate causes. The tombstone was built and written but never
read on the HTTP path; `utils/session_revocation`'s own docstring records the
deliberate decision to keep it out of `get_current_user` for SLA reasons and
leave callers to opt in — and no AI caller ever did. The refresh half is a
client/server contract mismatch: the server accepts the credential from a
cookie *or* the body, and the native client sent neither, relying on cookie
transport that native clients do not reliably perform.

**F03** — the AI store was added after the logout-callback convention was
established and simply never joined it. The unscoped storage key is the same
omission: every other per-session store is wiped on logout, so none of them
needed per-user namespacing, and the AI store inherited the assumption without
the wipe.

## 3. Fix / remediation

**F02, server:** new `get_current_user_active_session` dependency —
`get_current_user` plus an `is_session_revoked` tombstone check. Applied to
`/ai/chat`, the three `/ai/conversations*` routes, and the legacy
`/support/chat`. `/mcp` calls `get_current_user` directly rather than through
`Depends`, so the same check is spelled out inline in its middleware.

Kept as a **separate dependency**, not folded into `get_current_user`: that
function runs on every authenticated request, and the module documents why a
Redis round-trip does not belong there (auth-refresh <200 ms, dispatch <2 s
P95). An AI turn is a low-QPS, human-paced, high-consequence call, so one Redis
GET is affordable there in a way it is not on the location or dispatch path.

`/ai/config` deliberately stays on the plain dependency — global feature flags
and disclaimer copy, no customer data, no provider egress, polled on launch.
There is a test asserting this exception so it reads as a decision, not an
oversight.

**F02, client:** `shared/store/authStore.ts` logout now sends
`{ refresh_token }` explicitly instead of an empty POST.

**F03:** `aiChatStore` gains a `clearForSession()` action registered via
`registerLogoutCallback`, a `sessionGeneration` counter, and a per-user
conversation key (`spinr_ai_conversation_id:{userId}`). The legacy unscoped key
is deleted on the first sign-out after upgrade.

Two details the assessment specifically called for:

- **`clearForSession` deliberately does not reuse `stopStreaming`.** That path
  flushes a queued map pin into a fresh `submitMapPin`, which on logout would
  fire an authenticated request after sign-out and — during an account switch —
  send the previous rider's confirmed coordinates as the *new* account's
  message. The pin is dropped instead.
- **`abort()` alone is not sufficient**, which is what `sessionGeneration` is
  for. `streamChat`'s already-queued `onEvent` callbacks and the `finally`
  block still run after an abort and call `set(...)`, which would repopulate a
  store logout had just cleared. Every async callback captures the generation at
  start and drops its result if it has moved on.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface (backend auth + AI routes, rider-app store,
shared auth store).**

Greps performed:

- `grep -rn "is_session_revoked"` → previously only `routes/websocket.py` and
  tests. Now also `dependencies/__init__.py` and `ai/mcp_server.py`. The WS
  usage is untouched.
- `grep -rn "Depends(get_current_user)" routes/ai.py routes/support.py` → every
  AI route enumerated and individually decided (§3).
- `grep -rn "registerLogoutCallback"` → `rideStore` and `driverStore` already
  register; `aiChatStore` now joins them. The callback runner awaits each
  callback in a try/catch, so a throw here cannot block logout.
- `grep -rln "aiChatStore"` in rider-app tests → 5 files. Two mock the module
  (unaffected), two import it for real and needed the new `authStore` mock,
  one already mocked `authStore`.

Regressions considered:

- **Could the tombstone check log a live user out?** Only if a tombstone exists
  for their `session_id`, which happens only on their own explicit logout.
  Every ambiguous input — no `session_id` claim (all Firebase sessions), no
  tombstone, Redis unreachable — fails **open**, inherited from
  `is_session_revoked` and pinned by a test. Redis being down does not lock
  anyone out of the assistant.
- **Latency.** One Redis GET added to the AI turn only. Against a turn that
  already makes a provider round-trip measured in seconds, this is noise. No AI
  path has a stated P95 in the SLA table. Reasoned, not measured.
- **Could the explicit refresh token in the logout body leak?** It travels in
  the body of an authenticated HTTPS POST to our own API, to be revoked. The
  same value already travels in the refresh request body. No new exposure. It
  is not logged — the route reads it and passes it to `revoke_refresh_token`.
- **Could `clearForSession` wipe the config flags** and flash "coming soon" at
  the next rider? No — `enabled`/`mode`/`disclaimer` are deliberately left
  alone; they are global config, not session data. Called out in the code.
- **Could the generation guard swallow a legitimate late frame?** Only if the
  generation changed, which happens only on logout. Within a session the guard
  is a no-op.
- **`no-unsafe-finally`:** the first draft used `return` inside `finally`,
  which would discard an in-flight exception and trips eslint. Rewritten as a
  guarded block before commit.

No ride-state, money, dispatch, insurance-period or background-loop interaction.

## 5. User-experience effect

- **Rider / driver, normal use:** none. Nothing changes while signed in.
- **After tapping sign out:** the AI assistant stops working for that session
  immediately rather than for up to 15 more minutes — which is what "sign out"
  already means to a user. The retained refresh token is now actually revoked,
  so a stolen 30-day credential no longer resurrects the session.
- **Other devices stay signed in.** `should_tombstone` only tombstones when the
  logging-out token still owns `users.current_session_id`, so a second device
  that has since logged in is not taken down. That is an explicit acceptance
  criterion and it is preserved, not newly introduced.
- **Account switch on a shared device:** the second rider now opens a blank
  assistant instead of briefly seeing the first rider's conversation. This is
  the user-visible half of the finding.
- **Mid-session visibility:** a logout *is* the session ending, so yes by
  definition — but nothing changes for a user who does not sign out.
- No copy change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/dependencies/__init__.py` | New `get_current_user_active_session`; `is_session_revoked` import. | F02 server half. |
| `backend/routes/ai.py` | Four customer routes swapped to the new dependency; `/config` exception documented inline. | F02. |
| `backend/routes/support.py` | Legacy shim uses the new dependency. | F02 — it is an AI turn now. |
| `backend/ai/mcp_server.py` | Inline tombstone check (no `Depends` available). | F02. |
| `shared/store/authStore.ts` | Logout sends `{ refresh_token }`. | F02 client half. |
| `rider-app/store/aiChatStore.ts` | `clearForSession()`, logout registration, `sessionGeneration` guards, per-user conversation key, legacy-key cleanup. | F03. |
| `backend/tests/test_ai_session_revocation.py` | **New.** 9 tests: dependency behaviour, fail-open, and per-route gate assertions incl. the `/config` exception. | F02 cover. |
| `rider-app/store/__tests__/aiChatStore.test.ts` | `authStore` mock; key assertions namespaced; 7 new session-teardown tests. | F03 cover. |
| `rider-app/__tests__/aiChatDeviceLocation.test.ts` | `authStore` mock (the module now imports it at load). | Keep passing. |

## 7. Before / after

```python
# Before — routes/ai.py
async def ai_chat(..., current_user: dict = Depends(get_current_user)):
    # a signed-out token still drives a paid provider turn for ~15 min
```

```python
# After
async def ai_chat(..., current_user: dict = Depends(get_current_user_active_session)):
    # 401 ERR_SESSION_REVOKED once the logout tombstone exists
```

```ts
// Before — shared/store/authStore.ts   (server revokes nothing)
await api.post('/auth/logout');
```

```ts
// After
const currentRefreshToken = get().refreshToken ?? (await storage.getItem('refresh_token'));
await api.post('/auth/logout', currentRefreshToken ? { refresh_token: currentRefreshToken } : {});
```

```ts
// Before — rider-app/store/aiChatStore.ts
const CONVERSATION_KEY = 'spinr_ai_conversation_id';   // one key for every account
// ...no registerLogoutCallback anywhere in this file
```

```ts
// After
const conversationKeyFor = (userId) => userId ? `spinr_ai_conversation_id:${userId}` : LEGACY_CONVERSATION_KEY;
registerLogoutCallback(() => {
  useAiChatStore.getState().clearForSession();   // bumps sessionGeneration, aborts, drops the pin
  void AsyncStorage.removeItem(LEGACY_CONVERSATION_KEY).catch(() => undefined);
});
```

## 8. Rollback plan

**F02 server** — no dedicated flag, and one is not warranted: the check
fails open on every ambiguous input including a Redis outage, so the realistic
failure mode is "does nothing", not "locks users out". The operational lever if
it misbehaves is the existing `ai_assistant_enabled` switch (takes the AI
surface down entirely, no redeploy). Code-level rollback is a `git revert` of
the dependency swap; nothing is written, so there is no data to unwind.

**F02 client** — `git revert` restores the empty-body POST. Note that revoking
*more* refresh tokens is not a state that needs undoing: a revoked token was
one the user asked to sign out of.

**F03** — `git revert`. One caveat worth stating: after this ships, a rider's
conversation pointer lives under `spinr_ai_conversation_id:{userId}`. Reverting
makes the app read the old unscoped key again, which by then holds nothing, so
riders would lose the "reopen and rehydrate" convenience for one session and
start a new conversation. Server-side history is untouched — nothing is lost,
only the local pointer.

No migration, no schema change, no live-data mutation in any of the three.

## 9. Verification performed

- [x] **Blast-radius greps performed** — `is_session_revoked`,
      `Depends(get_current_user)` across the AI routes, `registerLogoutCallback`,
      and every rider-app test importing `aiChatStore`. Listed in §4.
- [x] **Every AI route individually decided**, not swept: four customer routes
      gated, `/ai/config` deliberately not, with a test pinning that choice so
      it is reviewable.
- [x] **Both assessment-specific traps handled and pinned by tests**: logout
      does not flush a queued map pin, and a post-logout stream frame cannot
      repopulate the store.
- [x] `ruff check` + `ruff format --check` clean on all changed Python files.
- [x] Reviewed the TypeScript by hand for the two defects a compiler would have
      caught: `return` inside `finally` (fixed — `no-unsafe-finally`), and
      `loadHistory`'s catch branch deleting the wrong storage key when the
      session ends mid-request (fixed by capturing the key once).
- [x] Reviewed against CLAUDE.md: the fail-open posture and its rationale are
      preserved verbatim from `utils/session_revocation`; no PII added to any
      log line; the dual-import pattern is preserved in every touched backend
      module.

## 10. What was NOT verified

- **No test suite was run — for either surface.** PyPI is unreachable (gateway
  403), so backend deps cannot be installed; `rider-app/node_modules` is absent
  and the npm registry is blocked too, so **jest and `tsc` could not run
  either**. The 9 new backend tests and 7 new rider-app tests are **unrun**,
  and the TypeScript is **untypechecked**. This is the weakest-verified change
  in this batch and CI must be green before it merges.
- **The rider-app test mocks were reasoned about, not executed.** In particular
  the `jest.mock('@shared/store/authStore', ...)` factory holds its state
  *inside* the factory specifically because a module-scope `const` would still
  be in its TDZ when `aiChatStore`'s module-level `registerLogoutCallback` runs
  during import. That reasoning is untested; if it is wrong, those two test
  files fail at import.
- **In-flight AI streams are NOT terminated server-side.** The assessment asks
  for this. The gate is checked when a turn starts, so a logout during an
  active stream lets that one turn finish (seconds, bounded by the 6-iteration
  cap). Doing better means periodically re-checking the tombstone inside the
  orchestrator loop, which puts a Redis call on the hot path — a deliberate
  deferral, not an oversight. **F02 is therefore only partly closed.**
- **Existing Spinr JWTs issued before F01 are unaffected by that finding's
  fix** and are only reachable by this one if their session is tombstoned —
  i.e. an anonymously-provisioned account that never signs out keeps its JWT
  until `exp`. Auditing for such accounts remains open follow-up (noted in
  F01's log too).
- **Native cookie behaviour was not exercised on a device.** The assessment
  flagged its own probe as conditional on that, and this change makes the body
  explicit precisely so the answer stops mattering — but "the native client
  never sent the cookie" is still unconfirmed on real hardware.
- **No physical account-switch reproduction.** F03 was identified from source
  and is fixed from source; the two-account-on-one-device test is exactly the
  manual check still owed, and it is the acceptance evidence the assessment
  asks for ("account B never renders or receives A's cached/streamed state").
- No latency measurement of the added Redis GET.
