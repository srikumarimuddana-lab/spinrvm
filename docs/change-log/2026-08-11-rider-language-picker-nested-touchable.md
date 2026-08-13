# Change Impact & Risk Log — Rider language picker: same nested-touchable press loss as the payment sheet

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude (session on `claude/wallet-payment-vehicle-selection-70tjn0`, PR #3655) |
| Surface(s) | rider-app |
| Domain (Sentry tag) | rides (settings surface) |
| PR / commit link | PR #3655 |
| Related issue or gap ID | Follow-up to `2026-08-11-rider-payment-sheet-done-button.md` — user-requested audit ("can this break other components? I faced it earlier") |

## 1. Issue / gap identified

Repo-wide audit for the payment-sheet bug's pattern (an interactive
Touchable nested inside parent `TouchableOpacity` wrappers in an RN `Modal`)
found exactly **one** other instance: the rider app's language picker in
`settings.tsx`. Its language rows are direct children of two Touchable
wrappers with **no ScrollView** in between, so the rows themselves (not just
a Done button) can lose presses on the New Architecture — the picker would
open but no language would be selectable.

## 2. Root cause

Same as the payment sheet (see companion log): Touchable-inside-Touchable
press delivery on the New Architecture. Corroborating detail: the **driver
app's** language modal (`driver-app/app/driver/settings.tsx`) was already
rewritten to the safe sibling-`Pressable` shape at some point — the same bug
was evidently hit and fixed there without sweeping the rider app.

## 3. Fix / remediation

Identical restructure to ride-options: backdrop becomes an absolute-fill
`Pressable` sibling under the sheet; sheet wrapper becomes a plain `View`;
rows and handlers untouched. Tap-outside-to-close, Android back, animation,
and visuals unchanged (`langOverlay` keeps the dim + flex-end layout).

## 4. Risk & impact on existing functionality

- Blast radius: **isolated** — inline modal in `settings.tsx`, no importers.
  Audit inventory (all `<Modal` + `activeOpacity={1}` sites across
  rider-app, driver-app, shared): every other site is a safe shape —
  sibling backdrops (`confirm-pickup`, `ride-status`, `CancelReasonSheet`,
  `CustomAlert`), plain-View overlays (`documents`, `profile`, `vehicle-info`,
  `SafetyOverlay`, `ForceUpdateOverlay`, driver home), Pressable+
  `stopPropagation` (driver settings), non-interactive wrapped content
  (login/otp/verify-email input-focus wrappers, account photo viewer), or a
  deliberate non-Modal overlay (driver `AlertDialog`). **Zero instances of
  the broken pattern remain after this commit.**
- No backend, state, or money interaction; language preference write path
  (`setLanguage`) unchanged.

## 5. User-experience effect

- Rider-facing: Settings → language picker rows become reliably tappable on
  New-Architecture builds. Pixel-identical otherwise. Visible mid-session
  only as "the picker works."

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app/settings.tsx` | Language modal: nested Touchables → View sheet over sibling Pressable backdrop; added `Pressable` import | Remove last instance of the New-Arch press-loss pattern |

## 7. Before / after

```tsx
# Before
<TouchableOpacity style={styles.langOverlay} activeOpacity={1} onPress={close}>
  <TouchableOpacity activeOpacity={1} style={styles.langSheet}>
    {LANGUAGES.map(… <TouchableOpacity onPress={pick}>…)}   // presses at risk
  </TouchableOpacity>
</TouchableOpacity>
```

```tsx
# After
<View style={styles.langOverlay}>
  <Pressable style={StyleSheet.absoluteFill} onPress={close} />
  <View style={styles.langSheet}>
    {LANGUAGES.map(… <TouchableOpacity onPress={pick}>…)}   // no Touchable ancestors
  </View>
</View>
```

## 8. Rollback plan

Same as the payment-sheet fix: ships via EAS Update; rollback is
`eas update:republish` of the prior update group — single step, no store
review, no data remediation (no persisted state touched).

## 9. Verification performed

- [x] `npx tsc --noEmit` clean.
- [x] Production bundle `expo export --platform android` run for this commit
  (result recorded in the commit message).
- [x] Blast-radius audit performed and inventoried above (this fix **is** the
  audit's output).
- [ ] No automated render test — same standing gap as ride-options (no
  harness for these screens).

## 10. What was NOT verified

- On-device tap behavior (no device/emulator in session) — after the OTA,
  smoke-test: Settings → language row selects and sheet closes.
- Whether the language rows were actually dead on the tester's device (the
  structural risk is confirmed; the live repro wasn't, since the report was
  about the payment sheet).

## 11. Sign-off

- [x] Rollback plan concrete and testable
- [x] Blast radius stated with full inventory
- [x] UX-effect field filled in
