# Change Impact & Risk Log

> Filed per `docs/templates/CHANGE_IMPACT_LOG.md` — this PR bundles 3 small,
> independent rider-app/backend fixes from the open `ACTION_ITEMS.md`
> backlog, so each gets its own numbered entry below rather than three
> separate files.

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (session `session_013hMEsuEPVu7gwAk21XB4hF`) |
| Surface(s) | backend, rider-app, shared |
| Domain (Sentry tag) | rides, ai |
| PR / commit link | https://github.com/srikumarimuddana-lab/spinrvm/pull/5177 |
| Related issue or gap ID | `ACTION_ITEMS.md` B9, AI17 (sub-items F3, F5) |

---

## Entry 1 — B9: friendly rider-app error for address/coordinate mismatch

### 1. Issue / gap identified

Booking's pickup/dropoff address↔coordinate mismatch check returned a raw
string 400 `detail` with no code; rider-app had no way to distinguish it,
so the raw backend sentence ("Pickup address and location don't match:
...") was shown verbatim instead of a friendly retry prompt.

### 2. Root cause

The check was added by a prior B9 fix without a corresponding rider-app
handler — a disclosed, not-yet-implemented gap noted in that fix's own
"Not verified" section.

### 3. Fix / remediation

Backend now returns `{"code": "PICKUP_ADDRESS_MISMATCH"|"DROPOFF_ADDRESS_MISMATCH",
"message": ...}`, matching the existing `OUTSIDE_SERVICE_AREA` pattern in
the same function. `shared/api/client.ts`'s `extractError` gained a branch
reading `code`/`message` directly off an object-shaped `data.detail`.
`rideStore.ts`'s `createRide` catch block maps the 2 new codes to a
friendly "double-check the pin" message; every other error path is
unchanged.

### 4. Risk & impact on existing functionality

`extractError` is used transitively by ~552 files via `getApiErrorMessage`
(flagged by the pre-commit hook's shared-component check) — both rider-app
and driver-app. The new branch is additive: it only changes behavior for
an object-shaped `data.detail`, a shape that previously fell through to a
**pre-existing, separately-confirmed bug** in the backend's
`http_exception_handler` (`error.message` becomes the raw object, an
"[object Object]" risk to any caller relying on it) — not introduced or
fixed by this PR, just not made worse. No other working (string/array
`detail`) path is touched. Blast radius: cross-surface (shared file) but
additive-only.

### 5. User-experience effect

Rider sees a friendly, actionable message instead of a raw sentence when
booking is rejected for an address/pin mismatch. Not mid-session-disruptive
— fires only at the moment of tapping "Book," never during an active ride.
Copy change only, reviewed for plain, non-technical, actionable tone.

### 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/booking.py` | 2 `HTTPException` sites gained a `code` field | Let the client distinguish pickup vs. dropoff mismatch |
| `shared/api/client.ts` | `extractError` gained an object-shaped-`detail` branch | Read `code`/`message` off the new structured error |
| `rider-app/store/rideStore.ts` | `createRide` catch block maps the 2 codes to friendly copy | User-facing fix |
| `backend/tests/test_create_ride_guard_clauses.py` | 2 existing tests updated for the new dict-shaped `detail` | They previously asserted `.lower()` on a plain string |

### 7. Before / after

```python
# Before
raise HTTPException(status_code=400, detail=f"Pickup address and location don't match: {reason}")

# After
raise HTTPException(status_code=400, detail={"code": "PICKUP_ADDRESS_MISMATCH", "message": f"Pickup address and location don't match: {reason}"})
```

### 8. Rollback plan

`git revert` — no data/migration/flag involved.

### 9. Verification performed

- [x] Automated tests: `python3 -m pytest tests/test_create_ride_guard_clauses.py` (26 passed)
- [x] `ruff check` clean
- [x] rider-app `tsc --noEmit` clean (0 errors)
- [ ] Manual repro steps followed in staging — not done this session
- [x] Blast-radius grep performed (searched all `extractError`/`getApiErrorMessage` importers)
- [ ] Feature-flagged — not needed; additive-only, no working path regresses

**Not verified**: no live device run (rider-app has no visual-regression tooling).

---

## Entry 2 — AI17/F3: safe fallback for unmapped AI-chat error codes

### 1. Issue / gap identified

`aiChatStore.ts`'s error handler fell back to the raw backend
`event.data.message` for any error `code` not present in its local
`ERROR_MESSAGES` map.

### 2. Root cause

The map covered only 4 of the codes `backend/ai/orchestrator.py` can emit;
2 more (`ai_misconfigured`, `provider_error`) silently relied on the
backend always sending friendly text, with nothing enforcing that
guarantee for future codes.

### 3. Fix / remediation

Added explicit entries for the 2 missing codes (worded identically to the
backend's current `GENERIC_ERROR_MESSAGE`, so no visible copy change
today) and removed the raw-message fallback entirely — an unmapped code
now always resolves to a generic, rider-app-owned default string.

### 4. Risk & impact on existing functionality

`ERROR_MESSAGES` and this `onEvent` handler are read only within this
file's own `sendMessage` flow — no other importer found. Isolated,
single-surface (rider-app) change.

### 5. User-experience effect

No visible change for the 2 currently-emitted codes (copy preserved
verbatim); closes a forward-looking gap where a future backend code change
could leak technical text into the chat.

### 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/store/aiChatStore.ts` | `ERROR_MESSAGES` map extended; raw-message fallback removed | Guarantee a rider-safe string for every error code, present and future |

### 7. Before / after

```ts
// Before
appendToAssistant(ERROR_MESSAGES[event.data.code] ?? event.data.message ?? ERROR_MESSAGES.default);

// After
appendToAssistant(ERROR_MESSAGES[event.data.code] ?? ERROR_MESSAGES.default);
```

### 8. Rollback plan

`git revert`.

### 9. Verification performed

- [x] rider-app `tsc --noEmit` clean
- [ ] Automated tests — no existing jest coverage for this file's streaming-event handling to extend (none found)
- [ ] Manual repro / live device run — not done this session

**Not verified**: no jest coverage existed for this code path to extend; no live device run.

---

## Entry 3 — AI17/F5: clear stale AI fare-quote Redis pin on conversation delete

### 1. Issue / gap identified

Deleting an AI conversation (PIPEDA right-to-delete) never cleared its
`ai:quote:{conversation_id}` Redis pin (15-min TTL), which the
orchestrator injects into the next turn's prompt as bookable "LAST QUOTE"
context.

### 2. Root cause

`delete_conversation` only ever touched the two Supabase tables
(`ai_conversations`, `ai_messages`); the Redis pin was added later by a
different code path (`tools_booking.py`) with no corresponding cleanup
wired in.

### 3. Fix / remediation

`delete_conversation` now best-effort deletes the pin after the DB rows
are removed — same fail-open contract as the pin's own writer
(`_pin_quote`): a Redis outage during cleanup must never turn an
already-committed delete into a reported failure.

### 4. Risk & impact on existing functionality

`delete_conversation` has exactly one caller (`routes/ai.py`'s DELETE
endpoint) — checked, no other route or background job calls it.
`redis_client.redis_delete` is already used this way (log + swallow)
throughout the codebase (`utils/driver_presence.py`,
`routes/drivers/_shared.py`) — this follows the same convention. Isolated,
single-surface (backend) change.

### 5. User-experience effect

None visible — purely a cleanup of server-side cached state. Closes a
window where a reused `conversation_id` could resurface an old priced trip
as bookable context.

### 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/ai/conversations.py` | `delete_conversation` now also deletes the quote pin, best-effort | Complete the blast radius of an existing PIPEDA delete path |
| `backend/tests/test_ai_conversations.py` | 2 new tests | Pin gets deleted with the right key; a Redis failure during cleanup doesn't fail the delete |

### 7. Before / after

```python
# Before
await db_supabase.delete_many("ai_messages", {"conversation_id": conversation_id})
await db_supabase.delete_one("ai_conversations", {"id": conversation_id})
return True

# After
await db_supabase.delete_many("ai_messages", {"conversation_id": conversation_id})
await db_supabase.delete_one("ai_conversations", {"id": conversation_id})
try:
    await redis_delete(_QUOTE_PIN_KEY_FMT.format(conversation_id=conversation_id))
except Exception:
    logger.error("ai quote pin cleanup failed", exc_info=True, extra={"conversation_id": conversation_id})
return True
```

### 8. Rollback plan

`git revert` — worst case reverts to the pre-existing (already-shipped)
stale-pin exposure window, not a new regression.

### 9. Verification performed

- [x] Automated tests: `python3 -m pytest tests/test_ai_conversations.py` (13 passed, incl. both new tests)
- [x] `ruff check` clean
- [ ] Manual repro steps followed in staging — not done this session
- [x] Blast-radius grep performed (confirmed `delete_conversation`'s one caller)

**Not verified**: no live Redis instance this session — exercised via
`redis_client.py`'s documented in-process-dict fallback.

---

## Sign-off

- [x] Rollback plan is concrete and testable for all 3 entries (plain `git revert`, no data/flag involved)
- [x] Blast radius is stated, not assumed, for all 3 entries
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
