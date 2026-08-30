# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-30 |
| Author | Claude Code (session: rider-textbox-visibility) |
| Surface(s) | rider-app |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/rider-textbox-visibility-d4w9lv` |
| Related issue or gap ID | Live-testing bug report (screenshot: ride-completed tip row, custom box black with no readable digits) |

## 1. Issue / gap identified

On the post-trip screen (`app/ride-completed.tsx`), typing a custom tip amount makes the
typed digits invisible: the box fills with a near-solid colour and the digits keep the same
colour as the fill. Reported from live app testing with a screenshot showing the filled
black box with only the caret visible.

## 2. Root cause

`styles.tipCustomActive` — applied the moment `customTip` is non-empty — sets
`backgroundColor: colors.text`, copying the selected-preset pill (`tipBtnActive`). The pill
also flips its label (`tipBtnTextActive: { color: '#FFF' }`); the custom box never got the
equivalent. So the `$` prefix stayed `colors.textDim` and the `TextInput` stayed
`colors.text` — i.e. `colors.text` text on a `colors.text` fill, a 1:1 contrast ratio.

This is broken in **both** themes, not just the screenshotted one: light `colors.text`
is `#1A1A1A`, dark is `#F2F2F7`, and the fill tracks the same token either way.

The same root cause makes the *preset* pills unreadable in dark mode: their
`tipBtnTextActive: '#FFF'` is correct against light mode's `#1A1A1A` fill but effectively
invisible against dark mode's `#F2F2F7` fill. Fixed here as part of the same style block.

## 3. Fix / remediation

Introduced a single `onInverse = colors.background` local in `createStyles` — `colors.text`
and `colors.background` are a guaranteed-contrasting pair in both palettes, whereas a
hard-coded `#FFF` is only correct in light mode. Applied it to the filled custom-tip box
(input text, `$` prefix, caret via `selectionColor`) and to the selected preset label.
Also replaced the hard-coded `placeholderTextColor="#BBB"` (≈1.9:1 on the white surface,
below WCAG 2.1 AA) with `colors.textDim`, matching the identical control in `app/wallet.tsx`.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one screen.** `createStyles` in `ride-completed.tsx` is a
  file-local function; none of the touched styles are exported or imported anywhere. Greps
  run: `tipCustom`/`tipBtn`/`tipDollar` across `rider-app/` (only this file), and
  `createStyles` (declared and used only in this file).
- No shared component, hook, or utility is modified. No backend, DB, ride-state, or money
  path is touched — the tip *value* logic (`effectiveTip`, `customTip` parsing, the Pay total,
  `rateRide`) is byte-for-byte unchanged; only colours and one `accessibilityLabel` changed.
- `shared/theme` is read, not modified — `colors.background` is an existing token in both
  `lightColors` and `darkColors`.
- **Not fixed here (pre-existing, same defect class):** five other screens paint
  `backgroundColor: colors.text` and pair it with a hard-coded or same-token foreground —
  `app/ai-assistant.tsx:776`, `app/chat-driver.tsx:493`, `app/driver-arrived.tsx:519`
  (`plateNum: '#FFF'`), `app/ride-in-progress.tsx:906` and `app/(tabs)/activity.tsx:651`
  (these two use `colors.surface`, which is `#1C1C1E` on a `#F2F2F7` fill in dark mode —
  readable, unlike the `#FFF` ones). Left alone to keep this change surgical; worth a
  follow-up sweep.

## 5. User-experience effect

- **Rider-facing, and visible mid-session** — this is the screen a rider sees immediately
  after a trip ends, before paying.
- Before: typing a custom tip produced an unreadable box; the rider could not confirm the
  amount they were about to be charged, on a screen that then charges their card.
- After: digits, `$`, and caret are readable against the filled box in both light and dark.
- Two other visible deltas: the "Other" placeholder is a darker grey (was `#BBB`), and in
  dark mode the selected preset's `$X` label becomes readable (was white-on-near-white).
- No copy or notification changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app/ride-completed.tsx` | Added `onInverse` local in `createStyles`; new `tipDollarActive` / `tipCustomInputActive` styles; `tipBtnTextActive` uses `onInverse` instead of `'#FFF'`; custom-tip `TextInput` gains conditional styles, `selectionColor`, `accessibilityLabel`, and a themed `placeholderTextColor` | The filled box's contents must contrast with its `colors.text` fill in both themes |
| `rider-app/__tests__/rideCompletedScreen.test.tsx` | Added `background` to the mock palette; two regression tests (filled box flips to the contrasting colour; empty box stays on the surface palette) | Pin the contrast flip so a future style edit can't silently reintroduce invisible text |

## 7. Before / after

```tsx
// Before — digits keep colors.text on a colors.text fill
<Text style={styles.tipDollar}>$</Text>
<TextInput style={styles.tipCustomInput} placeholderTextColor="#BBB" ... />

tipBtnTextActive: { color: '#FFF' },
tipDollar: { ..., color: colors.textDim },
tipCustomInput: { ..., color: colors.text },
```

```tsx
// After — contents flip with the fill
<Text style={[styles.tipDollar, customTip ? styles.tipDollarActive : null]}>$</Text>
<TextInput
  style={[styles.tipCustomInput, customTip ? styles.tipCustomInputActive : null]}
  placeholderTextColor={colors.textDim}
  selectionColor={customTip ? colors.background : colors.primary}
  ...
/>

const onInverse = colors.background;   // contrasting pair with colors.text in both themes
tipBtnTextActive: { color: onInverse },
tipDollarActive: { color: onInverse },
tipCustomInputActive: { color: onInverse },
```

## 8. Rollback plan

`git revert` of this commit is a complete rollback: the change is style-only, client-side,
and writes nothing. No migration, no `app_settings` value, no live data touched — nothing to
remediate at the data level. Not feature-flagged: the change is a contrast correction on a
currently-unreadable control, so shipping it dark would leave the bug in place for the
flag-off cohort. Reaching riders requires an OTA/EAS update like any other rider-app change,
so rollback is the same mechanism as rollout.

## 9. Verification performed

- [x] Blast-radius grep performed — `tipCustom*`, `tipBtn*`, `tipDollar`, `createStyles`,
      and `backgroundColor: colors.text` across `rider-app/app` + `rider-app/components`;
      results in §4.
- [x] Reviewed against `CLAUDE.md` conventions — no money arithmetic changed (tip parsing
      untouched, still the existing `parseFloat` path), no state-machine or WS surface.
- [x] Both modified files parse cleanly under a standalone `tsc --noEmit --noResolve`
      syntax pass (only expected unresolved-import/ambient-type errors remain).
- [ ] **Jest suite NOT run, and no typecheck/lint/production build run.** `rider-app`
      dependencies could not be installed in this session: `registry.npmjs.org` and
      `registry.yarnpkg.com` are both blocked by the session's egress policy (403 on
      CONNECT / E403 on GET). `npm run build` equivalent was therefore **not** run either.
      The two new tests are committed but **unexecuted** — they must pass in CI before merge.
- [ ] Manual repro in staging — not performed (no device/simulator in this environment).

## 10. What was NOT verified

- The two new regression tests have never been executed; if their DOM-navigation assumptions
  (`findAllByType(TextInput)` by placeholder, `'$'` as a bare string child) are wrong, they
  will fail in CI rather than pass vacuously — but that is unconfirmed here.
- No screenshot or visual diff. Per `CLAUDE.md` release gate #6, rider-app has **no**
  visual-regression tooling at all, so the resulting contrast was reasoned about from the
  palette tokens (`#1A1A1A`/`#FFFFFF` light, `#F2F2F7`/`#000000` dark — both ≥ 17:1), not
  observed on a device. This is a standing gap, not a new one.
- The dark-mode preset-pill fix in particular was never seen rendered; it is inferred from
  the token values.
- No check that an OTA/EAS update actually carries this to live testers.
