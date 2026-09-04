# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude Code (design-audit follow-up, round 2) |
| Surface(s) | rider-app, shared (driver-app unaffected — see §4) |
| Domain (Sentry tag) | n/a (pure UI, no backend/domain surface) |
| PR / commit link | (filled in on PR open — branch `claude/rider-app-button-card-input-round1`) |
| Related issue or gap ID | Follow-up to `docs/change-log/2026-09-04-shared-button-card-input-primitives.md` (PR #4950) — that PR explicitly deferred `Card`/`Input` wiring and further `Button` migration as "future opportunistic work"; this is that work. |

## 1. Issue / gap identified

PR #4950 landed `shared/components/{Button,Card,Input}.tsx` but left `Card` and `Input` with **zero consumers**, and `Button` with only 3 of the CTA buttons the design audit found. `rider-app/app/emergency-contacts.tsx` (hand-rolled `TextInput`+label pairs and Cancel/Save `TouchableOpacity`s), `rider-app/components/FareQuoteCard.tsx` / `BookingProposalCard.tsx` (identical hand-rolled card `View` wrapper, byte-for-byte the same rule Card.tsx was extracted from), and `rider-app/app/reactivate-account.tsx` / `legacy-consent-notice.tsx` (near-identical primary CTA `TouchableOpacity` blocks) were all still on their own local styling.

## 2. Root cause

Same root cause as #4950: primitives existed but hadn't been wired into their real call sites yet — landing the primitive and adopting it were deliberately split into separate PRs per that PR's stated scope.

## 3. Fix / remediation

- **`shared/components/Input.tsx`**: added an optional `labelStyle?: StyleProp<TextStyle>` prop (mirrors `Button`'s existing `textStyle` escape hatch) so a consumer can override the label's font — needed because `emergency-contacts.tsx`'s label used `fontFamily: 'PlusJakartaSans_600SemiBold'`, which `Input`'s default label style didn't set. Added a corresponding unit test in `shared/components/__tests__/Input.test.tsx`.
- **`rider-app/app/emergency-contacts.tsx`**: migrated both form fields (Full Name, Phone Number) to `Input` (via `labelStyle`+`style` overrides that reproduce the original styling exactly — see §7) and the Cancel/Save button pair to `Button` (`variant="secondary"`/`"primary"`, `size="md"`, `fullWidth={false}`).
- **`rider-app/components/FareQuoteCard.tsx`** and **`BookingProposalCard.tsx`**: migrated the outer card `View` to `Card padding="md"`, keeping only the chat-bubble-alignment leftover (`marginLeft`, `gap`, `alignSelf`) in the local `card` style.
- **`rider-app/app/reactivate-account.tsx`** and **`legacy-consent-notice.tsx`**: migrated the primary "Reactivate my account" / "Accept" `TouchableOpacity` to `Button` (default `variant="primary" size="lg"`), each keeping only the below-button `marginBottom` spacing locally.

## 4. Risk & impact on existing functionality

- **Blast radius: rider-app only, isolated to the 5 edited screens/components plus the 1 shared file.** Grepped `rg "@shared/components/(Button|Card|Input)"` across the whole repo (before and after): consumers are now `become-driver.tsx`, `report-safety.tsx`, `ride-options.tsx` (from #4950, untouched by this change), plus this round's 5 files. **`driver-app` has zero consumers of any of the three** (`rg` returned no matches in `driver-app/`) — confirmed via grep, not run through driver-app's own `tsc` in this round (see §10 for why that's an acceptable gap here: the only shared-file edit, `Input.tsx`, is a strictly-additive optional prop with no existing driver-app import to break).
- `Input`'s new `labelStyle` prop is additive-only (optional, defaults to `undefined`, spread after the existing `styles.label` in the style array so it only ever adds/overrides — never removes — existing label styling). No existing `Input` call site is broken by this, because (per #4950) there were none before this PR.
- `Card`'s `padding="md"` token (`SPACING.md` = 16) differs from `FareQuoteCard`/`BookingProposalCard`'s original literal `padding: 14` — a 2px cosmetic difference, the same convergence `Card.tsx`'s own doc comment already documents and accepts. No other file imports these two card components' internal `styles.card` (both are default-exported screen-level components, not shared elsewhere) — grepped `rg "FareQuoteCard|BookingProposalCard"` to confirm each is used in exactly one place (the AI-chat message-rendering flow), so the visual delta's blast radius is that one rendering path.
- `Button`'s `lg` size (`height: 54, borderRadius: 14`) differs from `reactivate-account.tsx`/`legacy-consent-notice.tsx`'s original literal (`height: 56, borderRadius: 16`) — same disclosed 2px-class cosmetic rounding pattern #4950 already established for its three migrated buttons.
- **Behavioral (non-cosmetic) change, disclosed per gate #5**: on `reactivate-account.tsx` and `legacy-consent-notice.tsx`, the primary button no longer additionally dims to `opacity: 0.6` while its request is in flight — `Button`'s `disabledDim` style only applies when `disabled && !loading` (true `disabled`, not `loading`), and both call sites now use `loading={busy}`/`loading={accepting}` rather than `disabled`. The spinner swap-in remains (same visual "something is happening" cue), just without the extra opacity dim. This is the identical, already-accepted trade-off #4950 §5 documents for `ride-options.tsx`'s Confirm button ("no longer additionally dims while booking — the spinner alone is the loading affordance").
- `emergency-contacts.tsx`'s Save button: `disabled={saving}` was dropped in favor of `loading={saving}` alone — functionally identical (`Button` computes `isDisabled = disabled || loading`, so `onPress` is still blocked while saving), same non-dimming caveat as above applies to this button's opacity too.
- No backend, database, ride-state, payment, or dispatch code is touched. Nothing in this diff reads/writes a Supabase table, a WebSocket event, or a background loop. Emergency contacts, account reactivation, and legacy-consent-acceptance flows all call the same `api.post`/`api.get`/`api.delete` functions with the same payloads as before — no request/response contract changed.
- A **pre-existing test broke**, caused by the migration itself (not a pre-existing bug): `rider-app/__tests__/reactivateAccountScreen.test.tsx`'s "shows the in-flight spinner..." test used `renderer.root.findByProps({ accessibilityLabel: ... })` to grab the button and assert `.props.disabled === true`. `findByProps` is non-deep — once `Button` wraps the `TouchableOpacity`, that call now resolves to `Button`'s own composite instance (which doesn't re-expose `disabled` at all), not the inner `TouchableOpacity` that actually carries it. Fixed by adding a `findAllByType(TouchableOpacity)` lookup for the real element instead, matching the pattern `__tests__/emergencyContactsScreen.test.tsx`'s own `findButtonByText` helper already used before this PR. `emergencyContactsScreen.test.tsx` and `legacyConsentNoticeScreen.test.tsx` needed no equivalent fix — confirmed by running the full suite (see §9).

## 5. User-experience effect

Rider-facing only, on 5 specific screens/components (Emergency Contacts form, the AI-chat fare-quote and booking-proposal cards, Reactivate Account, and the legacy-consent notice). Not visible mid-session to someone already on one of these screens before the update ships — none of these screens has state that would show a stale button/card mid-interaction; a fresh screen mount picks up the new styling.

What actually changes, per surface — cosmetic only except the loading-dim caveat called out below:

| Surface | Before | After |
|---|---|---|
| `emergency-contacts.tsx` form fields | Hand-rolled label+`TextInput` | Same visual result via `Input` (labelStyle/style overrides reproduce the exact original rule set — see §7) |
| `emergency-contacts.tsx` Cancel/Save buttons | 14px radius, Save dims to 0.6 opacity while saving | 12px radius (shared `md`), Save no longer additionally dims while saving (spinner-only, see §4) |
| `FareQuoteCard.tsx` / `BookingProposalCard.tsx` | 14px padding | 16px padding (shared `md` token, 2px cosmetic) |
| `reactivate-account.tsx` / `legacy-consent-notice.tsx` primary button | 56px tall, 16px radius, dims to 0.6 opacity while busy | 54px tall, 14px radius (shared `lg`), no longer additionally dims while busy (spinner-only, see §4) |

Text content, tap targets (all buttons stay full-width or intentionally inline per their original layout), and every `onPress`/`onChangeText` handler are unchanged.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/components/Input.tsx` | Added optional `labelStyle` prop | First real consumer (`emergency-contacts.tsx`) needs a brand `fontFamily` override on the label |
| `shared/components/__tests__/Input.test.tsx` | Added a test for `labelStyle` | Cover the new prop |
| `rider-app/app/emergency-contacts.tsx` | Migrated Full Name/Phone `TextInput`s to `Input`; migrated Cancel/Save to `Button`; removed now-orphaned `TextInput` import | Round-2 opportunistic migration |
| `rider-app/components/FareQuoteCard.tsx` | Migrated outer card `View` to `Card padding="md"` | Round-2 opportunistic migration |
| `rider-app/components/BookingProposalCard.tsx` | Migrated outer card `View` to `Card padding="md"` | Round-2 opportunistic migration |
| `rider-app/app/reactivate-account.tsx` | Migrated primary CTA to `Button`; removed now-orphaned `ActivityIndicator` import and `btnDisabled` style | Round-2 opportunistic migration |
| `rider-app/app/legacy-consent-notice.tsx` | Migrated primary CTA to `Button`; removed now-orphaned `btnDisabled` style | Round-2 opportunistic migration |
| `rider-app/__tests__/reactivateAccountScreen.test.tsx` | Fixed the in-flight-spinner test's `disabled` assertion to look up the real `TouchableOpacity` instead of `Button`'s outer instance | Existing pinned test broke — see §4 |

## 7. Before / after

`rider-app/app/emergency-contacts.tsx` (Full Name field — representative of both migrated fields):

```tsx
# Before
<Text style={styles.formLabel}>Full Name</Text>
<TextInput
  style={styles.formInput}
  placeholder="e.g. Sarah Johnson"
  placeholderTextColor={colors.textDim}
  value={name}
  onChangeText={setName}
  autoCapitalize="words"
/>
```

```tsx
# After
<Input
  label="Full Name"
  labelStyle={styles.formLabel}
  style={styles.formInput}
  placeholder="e.g. Sarah Johnson"
  value={name}
  onChangeText={setName}
  autoCapitalize="words"
/>
```

(`styles.formLabel`/`styles.formInput` are unchanged local style objects, now passed as overrides on top of `Input`'s defaults instead of directly on a bare `Text`/`TextInput` — this is why the rendered result is unchanged: every property `Input`'s own default doesn't already match is still supplied verbatim. `placeholderTextColor` was dropped from the call site because `Input` already hardcodes the identical `colors.textDim` value internally.)

`rider-app/app/reactivate-account.tsx` (representative of both migrated primary buttons):

```tsx
# Before
<TouchableOpacity
  style={[styles.primaryBtn, busy && styles.btnDisabled]}
  onPress={handleReactivate}
  disabled={busy}
  activeOpacity={0.85}
  accessibilityLabel="Reactivate my account"
>
  {busy ? <ActivityIndicator color="#fff" size="small" /> : <Text style={styles.primaryBtnText}>Reactivate my account</Text>}
</TouchableOpacity>
```

```tsx
# After
<Button
  style={styles.primaryBtn}
  textStyle={styles.primaryBtnText}
  loading={busy}
  onPress={handleReactivate}
  activeOpacity={0.85}
  accessibilityLabel="Reactivate my account"
>
  Reactivate my account
</Button>
```

## 8. Rollback plan

No feature flag — same reasoning as #4950's gate-#3 analysis: `Card`/`Input` still have a small, single-digit consumer count after this PR (not the 3+ pages the gate's flag requirement is keyed to), and `Button`'s new call sites are likewise few and independent.

If a problem surfaces on any one of the 5 edited files: revert that single file's hunk. `git revert` is sufficient — no Stripe charge, wallet delta, or ride-state row is touched by this change, so there is no live-data remediation step, only a code revert. Each file's edit is an independent, self-contained hunk; reverting one does not require reverting the others or `shared/components/Input.tsx`'s new `labelStyle` prop (additive, harmless to leave in place even if a consumer is reverted).

## 9. Verification performed

- [x] Automated tests run: full rider-app `npx jest` suite, run twice after the fix (once isolated to the fixed file, once for the whole suite) — **143 suites / 1964 tests, all passing**, plus the new `Input.test.tsx` `labelStyle` case specifically re-run in isolation to confirm it passes on its own.
- [x] `npx tsc --noEmit` run in `rider-app` — clean, zero errors (run twice, before and after the test fix, both clean).
- [x] Production build check: `npx expo export --platform web` (this repo's `build:web` script, the closest "real production build" rider-app has — it is an Expo Router app, not a Next.js/Vite app with a `next build`/`vite build` equivalent) — **succeeded**, produced `dist/` (gitignored, not committed) with 3 web bundles, no errors. This is the real production bundler/exporter, not the dev server — satisfies CLAUDE.md's "a passing dev server ... alone is not equivalent" requirement.
- [x] Blast-radius grep performed: `rg "@shared/components/(Button|Card|Input)"` across the whole repo (rider-app, driver-app, admin-dashboard, shared) before and after — confirmed the consumer list matches §4 exactly, and `driver-app` has zero matches.
- [x] Reviewed against CLAUDE.md conventions: no hardcoded hex outside the pre-existing convention already established by `Button.tsx`/#4950 (`#FFFFFF` on-color text). No new colors introduced — every style object reused is the screen's own pre-existing `createStyles(colors)` output, unchanged in value, just re-targeted at a different prop (`labelStyle`/`style`/`textStyle` instead of a bare `Text`/`TouchableOpacity`).
- [ ] Manual repro / staging check — not performed (no staging environment available in this session); relying on automated tests + `tsc` + the property-by-property before/after comparison in §7.

## 10. What was NOT verified

- **No visual regression tooling exists for rider-app** (CLAUDE.md is explicit: rider-app and driver-app have none). Every cosmetic delta in §5 (2px padding/radius/height rounding, the loading-dim removal) was reasoned about from source styles, not screenshotted. A human should eyeball Emergency Contacts, the AI-chat fare-quote/booking cards, Reactivate Account, and the legacy-consent screen before/after in a simulator or device, with particular attention to the Save/Reactivate/Accept buttons' in-flight (loading) state, since that's where the one behavioral (not just cosmetic) change lives.
- **`driver-app`'s own `tsc --noEmit` was not re-run in this round**, unlike #4950 (which needed and made a `driver-app/tsconfig.json` fix because it type-checks all of `../shared/**`). This round's only `shared/` edit is `Input.tsx`'s new optional `labelStyle` prop — strictly additive, and `driver-app` has zero imports of `Input` (confirmed by grep, §4), so no driver-app type-check regression is expected; this is a reasoned judgment call rather than a run command, made to avoid a second ~1GB+ `yarn install` under this session's disk constraints (rider-app's own install briefly exhausted the shared session disk earlier in this task before space was freed).
- Not tested against a live Supabase/backend — none of the underlying `api.get`/`api.post`/`api.delete` calls or their payloads changed in this diff, so this is a low-risk gap, but no end-to-end app run (Expo Go / simulator) was done in this session.
- No accessibility audit beyond code-level: `Button`/`Input` already set `accessibilityRole`/`accessibilityState`/label props per #4950; this PR didn't add or change any accessibility-relevant prop, and none of it was verified with an actual screen reader in this round either.
