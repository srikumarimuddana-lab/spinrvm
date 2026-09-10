# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (session `session_013hMEsuEPVu7gwAk21XB4hF`) |
| Surface(s) | backend, rider-app, admin-dashboard, shared |
| Domain (Sentry tag) | ai |
| Related gap ID | `ACTION_ITEMS.md` AI17, sub-item F4 |

## 1. Issue / gap identified

`get_fare_quote()` (`backend/ai/tools_booking.py`) omits price entirely for
any vehicle type with no drivers online — for a partial outage the type is
just named in `unavailable_vehicle_types` with no price attached, and for a
total outage the whole call returns `{"no_drivers": true, "quotes": []}`
with no fare fields at all. `rider-app/app/ride-options.tsx` already shows
a real, live-computed price for every vehicle type regardless of
availability (dimmed, "No drivers nearby", booking disabled). Product
decision (confirmed via `AskUserQuestion`): the AI chat should match the
rider-app screen's existing pattern, not the other way around.

## 2. Root cause

`get_fare_quote()`'s quote-building loop was written to `continue` past any
`estimate.available == False` entry rather than pricing it, so the
assistant never had price data to share for an unavailable type in the
first place — not a deliberate privacy/safety choice, just how the loop was
originally built (confirmed via code search: no comment anywhere states a
"don't quote what can't be booked" rationale).

## 3. Fix / remediation

Added `ai_fare_quote_show_unavailable_enabled` (bool, default **FALSE** —
ships dark, since this is new behaviour, not a preserved default; migration
410). When on, `get_fare_quote()`:
- builds a priced quote object (`available: false`) for an unavailable
  vehicle type instead of omitting it, for both the partial-outage and
  total-outage cases;
- always computes `recommended_vehicle_type_id` and the booking-shortcut
  Redis pin from **available options only** — an unavailable option is
  never recommended or made bookable through the pinned-quote "book it"
  shortcut (rule 6c), regardless of the flag;
- extends the tool's `note` (the model's per-call guidance) so it never
  offers to book a priced-but-unavailable option, and for a total outage
  explicitly forbids calling `propose_ride_booking` from that result.

`shared/types/ai.ts`'s `FareQuoteOption` gained an additive, optional
`available?: boolean | null` field. Both chat surfaces that render a
`fare_quote` action — `rider-app/components/FareQuoteCard.tsx` and
`admin-dashboard/src/app/dashboard/ai-console/page.tsx` — now dim an
`available: false` option, show "No drivers nearby" instead of
ETA/capacity, and disable tapping it, mirroring `ride-options.tsx`'s
existing `AnimatedVehicleCard` treatment.

## 4. Risk & impact on existing functionality

- **Blast radius, backend**: `get_fare_quote()`'s only caller is the AI
  tool registry (`backend/ai/tools.py`'s `register()`); no other function
  calls it directly. Flag off (default) keeps every code path byte-for-byte
  identical to pre-F4 — pinned by a new assertion on the existing
  happy-path test (`assert "available" not in q`) plus the full existing
  `TestFareQuote` suite (13 tests, all passing unmodified).
- **Blast radius, `FareQuoteOption`**: grepped every consumer (33 files).
  The two UI renderers (rider-app, admin-dashboard) are updated in this
  change. Everything else touching the type is either this change's own
  tests, docs/change-log entries, or backend files that only *produce* the
  type (`tools_booking.py`, `tools_account.py`, `orchestrator.py`,
  `tools.py`) — none of those *read* `available`, so they're unaffected by
  its addition.
- **Booking safety invariant** (the critical one): an unavailable option
  showing a price must never become bookable through it. This holds two
  ways, both independent of the flag: (a) `recommended_vehicle_type_id`
  and the priced-quote pin are computed from `available_quotes` (filtered),
  never raw `quotes`; (b) the no-drivers pin keeps its pre-existing safe,
  endpoints-only shape (no `vehicle_type_id`/`total`) whenever nothing is
  bookable, exactly as before this change. Both covered by new tests
  (`test_show_unavailable_flag_prices_a_partially_unavailable_type`,
  `test_show_unavailable_flag_all_unavailable_still_shares_priced_quotes`).
  `propose_ride_booking` itself is unchanged — it doesn't re-verify
  availability today (neither does the rider-app booking-confirm flow: the
  guard there is the same client-side disabled-button pattern this change
  mirrors), so a determined bypass of the UI/model guidance lands exactly
  where a bypass of `ride-options.tsx`'s disabled Confirm button would:
  the ride enters the normal `searching` state and follows the existing
  no-drivers-found auto-cancel flow. No new/weakened guard either way.
- **Global flag, two surfaces**: the flag is a single global
  `app_settings` row, not scoped per-surface — turning it on affects both
  the rider-app AI assistant and the admin AI console simultaneously (they
  share the same `get_fare_quote` tool). Both were updated in the same
  commit for that reason (found via the blast-radius grep for
  `FareQuoteOption` consumers, not part of the original decision scope,
  but required for the two surfaces to stay consistent once the flag is
  on).

## 5. User-experience effect

None by default (flag is `FALSE`). Once an admin flips it on:
- **Riders**: the AI assistant's fare-quote card can now show a dimmed,
  priced "No drivers nearby" option instead of silently omitting that
  vehicle type — matching what they already see on the ride-options
  screen. For a total outage, the assistant's spoken/text reply may now
  mention prices "for reference" while being explicit that nothing is
  currently bookable (a wording change to the model's guidance, not a new
  UI surface — the no-drivers case still emits no `_client_action`/card,
  same as before).
- **Admin AI-console users**: same dimmed/disabled treatment on the
  console's own quote-card rendering.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/410_ai_fare_quote_show_unavailable.sql` | New `settings.ai_fare_quote_show_unavailable_enabled` column, `DEFAULT FALSE` | Persist the flag |
| `backend/schemas.py` | `AppSettings` gained the field, default `False` | Settings read-model |
| `backend/routes/admin/settings.py` | `SettingsUpdateRequest` gained the field | Admin write-allowlist |
| `backend/tests/test_admin_settings_write_allowlist_drift.py` | Added the column name to `KNOWN_SETTINGS_COLUMNS` | Keep the drift guard accurate |
| `backend/ai/tools_booking.py` | `get_fare_quote()`: prices unavailable options behind the flag; recommendation/pin stay available-only | The actual behaviour change |
| `backend/tests/test_ai_tools_booking.py` | 1 new assertion on the existing default-path test + 2 new tests for the flag-on partial/total-outage cases | Cover both directions |
| `shared/types/ai.ts` | `FareQuoteOption` gained optional `available?: boolean \| null` | Additive shared-type change |
| `rider-app/components/FareQuoteCard.tsx` | Dims + disables an `available: false` option, "No drivers nearby" copy | Rider-facing parity with ride-options.tsx |
| `rider-app/components/__tests__/FareQuoteCard.test.tsx` | New — 3 tests (unavailable disabled, available still bookable, legacy quote with no `available` field treated as available) | Cover the new UI branch |
| `admin-dashboard/src/app/dashboard/ai-console/page.tsx` | Same dim/disable treatment | Blast-radius fix — same shared tool feeds this surface |
| `docs/change-log/2026-09-10-ai17-f4-fare-quote-availability-parity.md` | This file | Mandatory for a live-tested, money-adjacent surface |
| `ACTION_ITEMS.md` | AI17/F4 closed | Backlog hygiene |

## 7. Before / after

```python
# Before
for e in estimates:
    vt = e.get("vehicle_type") or {}
    if not e.get("available"):
        if vt.get("name"):
            unavailable.append(vt["name"])
        continue
    # ...build and append a priced quote...

# After (flag on; flag off is byte-identical to Before)
for e in estimates:
    vt = e.get("vehicle_type") or {}
    is_available = bool(e.get("available"))
    if not is_available:
        if vt.get("name"):
            unavailable.append(vt["name"])
        if not show_unavailable:
            continue
    # ...build and append a priced quote...
    if show_unavailable:
        quote["available"] = is_available
```

```tsx
{/* Before (rider-app/components/FareQuoteCard.tsx) */}
<TouchableOpacity onPress={() => onSelect(option)} disabled={disabled} ...>

{/* After */}
const isUnavailable = option.available === false;
<TouchableOpacity
  style={[styles.option, isUnavailable && styles.optionUnavailable]}
  onPress={() => onSelect(option)}
  disabled={disabled || isUnavailable}
  ...
>
```

## 8. Rollback plan

`git revert` for all code changes; the migration's own rollback
(`DROP COLUMN IF EXISTS ai_fare_quote_show_unavailable_enabled`) restores
exactly today's behaviour, since the read site's explicit `False` default
already matches production with the column absent. No data migration
either direction — this is a chat-response-shaping and UI-rendering flag,
never a money/ride-state field. Fastest rollback if the flag is ever
flipped on and causes trouble: flip it back to `False` in the admin
settings UI — no deploy needed, matches this repo's `app_settings`-in-DB
pattern.

## 9. Verification performed

- [x] Automated tests: `python3 -m pytest tests/test_ai_tools_booking.py
  tests/test_ai_orchestrator.py tests/test_ai_mcp_coverage.py
  tests/test_ai_mcp.py tests/test_admin_settings_write_allowlist_drift.py`
  — **190 passed, 1 skipped** (pre-existing, unrelated skip)
- [x] `ruff check` + `ruff format --check` on all touched backend files —
  clean
- [x] rider-app: `yarn jest components/__tests__/bookingProposal.test.ts
  components/__tests__/FareQuoteCard.test.tsx
  __tests__/aiAssistantScreen.test.tsx __tests__/rideOptionsScreen.test.tsx`
  — **214 passed**
- [x] TypeScript: `tsc --noEmit` clean for both rider-app and
  admin-dashboard on the touched files
- [x] Admin-dashboard: **real `npm run build`** run (not just
  `tsc --noEmit`) — exit 0, no compile errors, only one pre-existing,
  unrelated ESLint warning (`useEffect`/`setState` pattern at a line this
  change didn't touch)
- [x] Blast-radius grep performed: every consumer of `get_fare_quote`
  (one — the tool registry) and `FareQuoteOption`/`FareQuoteCard` (33
  files) checked; both real UI renderers updated, everything else
  confirmed to not read the new field
- [ ] Manual repro / staging check — not performed this session (no live
  Supabase/staging access from this environment)

**Not verified**: no live device/browser run confirming the dimmed/disabled
card actually renders correctly on a real rider-app build or in the admin
console's live UI — reasoned about and unit-tested, not screenshotted (this
repo has no visual-regression tooling for rider-app; admin-dashboard's
Playwright visual-regression suite doesn't cover the `ai-console` page, so
it provides no coverage here either). No admin-dashboard test file exists
for the `ai-console` page today, and this change doesn't add one — the fix
there is small and mirrors the already-tested rider-app component, but it
is genuinely un-unit-tested; flagging rather than silently relying on
"looks fine by inspection."
