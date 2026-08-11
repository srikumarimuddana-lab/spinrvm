# Change Impact & Risk Log — Rider payment-method sheet "Done" button unresponsive

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude (session on `claude/wallet-payment-vehicle-selection-70tjn0`) |
| Surface(s) | rider-app |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/wallet-payment-vehicle-selection-70tjn0` |
| Related issue or gap ID | Live-testing report 2026-08-11 (wallet payment selection stuck) |

## 1. Issue / gap identified

During live app testing, a rider on the vehicle-selection screen opened the
payment-method sheet, selected Spinr Wallet (checkmark appeared), and tapped
**Done** — nothing happened. The sheet never closed, the flow could not be
completed, and no error appeared anywhere (client or backend), because the
tap never reached the button's handler at all.

## 2. Root cause

The Done button was a `TouchableOpacity` nested directly inside **two parent
`TouchableOpacity` wrappers** (the sheet container and the dimmed backdrop) in
`ride-options.tsx`'s payment `Modal`. Under React Native's New Architecture
(this app: `runtimeVersion: '2.0.0'`, bumped specifically for New Arch),
presses on a Touchable nested directly under other Touchables can fail to be
delivered on some devices. The payment **rows** kept working because they sit
inside the `ScrollView` and receive touches through the scroll responder
path, which masks the problem — matching the observed symptom exactly
(row selectable, Done dead).

Caveat stated honestly: this mechanism is strongly indicated by the symptom
pattern and the known New-Arch touchable-nesting behavior, but was **not
reproduced on a physical device from this environment**. The restructure
below removes the entire nested-touchable class regardless of which device
condition triggers it, and changes nothing else.

## 3. Fix / remediation

Restructured the modal so no Touchable sits above the Done button:
- Backdrop: was the outermost `TouchableOpacity` parent → now an
  absolute-fill `Pressable` **sibling** rendered *under* the sheet.
- Sheet container: was a `TouchableOpacity` (with `activeOpacity={1}`, no
  `onPress`, used only to swallow backdrop taps) → now a plain `View`
  (a `View` above the backdrop swallows those taps by itself).
- Done button, rows, styles, animation, and all handlers: unchanged.

Behavior preserved: tap-outside-to-close (backdrop `Pressable`), Android
back button (`onRequestClose`), slide animation, identical visuals (same
`modalOverlay` / `paymentModal` styles).

## 4. Risk & impact on existing functionality

- Blast radius: **isolated, single surface** — this modal is defined inline
  in `rider-app/app/ride-options.tsx`; nothing imports it.
  - Grep `styles.modalOverlay} activeOpacity={1}` across `rider-app/`:
    this was the **only** occurrence of the nested-backdrop pattern; no other
    screen shares it.
  - `styles.modalOverlay` / `styles.paymentModal` are used only by this modal
    in this file (the promo and vehicle sheets are `@gorhom/bottom-sheet`
    components, not RN `Modal`s).
- Could it regress a working flow? The only behavior this subtree owned was
  (a) tap-outside closes, (b) taps on the sheet body do nothing, (c) Done
  closes. (a) is reimplemented via the sibling `Pressable`; (b) falls out of
  the sheet `View` sitting above the backdrop; (c) is untouched. The one
  intentional loss is the (invisible) opacity flicker the old outer
  Touchables could produce on press — cosmetic, and both had
  `activeOpacity={1}` anyway, i.e. no visible feedback existed.
- No backend, state-machine, wallet-delta, or background-loop interaction:
  zero changes to what is sent to the backend. `selectedPayment` was always
  set by the row tap itself; Done only ever closed the sheet.

## 5. User-experience effect

- **Rider-facing.** Riders can now dismiss the payment sheet via Done and
  proceed to book with the selected method (wallet, card, corporate).
- Visible mid-session: yes — anyone currently stuck on this sheet is unstuck
  after the update reaches their device. No layout or copy change; the sheet
  looks pixel-identical.
- Until the update is delivered, the in-app workaround is to tap the dimmed
  area above the sheet (or Android back) after selecting a method — the
  selection is already applied by the row tap.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app/ride-options.tsx` | Payment modal: backdrop `TouchableOpacity`→sibling `Pressable`; sheet `TouchableOpacity`→`View`; added `Pressable` import + explanatory comment | Un-nest the Done button from parent Touchables so its presses register on the New Architecture |

## 7. Before / after

```tsx
# Before
<Modal visible={showPaymentSheet} animationType="slide" transparent onRequestClose={…}>
  <TouchableOpacity style={styles.modalOverlay} activeOpacity={1} onPress={close}>
    <TouchableOpacity activeOpacity={1} style={styles.paymentModal}>
      …rows…
      <TouchableOpacity style={styles.paymentDoneBtn} onPress={close}>  // press not delivered
        <Text>Done</Text>
      </TouchableOpacity>
    </TouchableOpacity>
  </TouchableOpacity>
</Modal>
```

```tsx
# After
<Modal visible={showPaymentSheet} animationType="slide" transparent onRequestClose={…}>
  <View style={styles.modalOverlay}>
    <Pressable style={StyleSheet.absoluteFill} onPress={close} />  // backdrop: sibling, under sheet
    <View style={styles.paymentModal}>
      …rows…
      <TouchableOpacity style={styles.paymentDoneBtn} onPress={close}>  // no Touchable ancestors
        <Text>Done</Text>
      </TouchableOpacity>
    </View>
  </View>
</Modal>
```

## 8. Rollback plan

- Delivery for this fix is an **EAS Update (OTA)** on the app's channel — no
  store build needed (`expo-updates` configured, `runtimeVersion: '2.0.0'`).
- Rollback without redeploy: **republish the previous update group** on the
  same channel (`eas update:republish` / dashboard one-click), which reverts
  every device to the prior JS bundle on next launch. No data-level
  remediation needed — the change touches no persisted state, no money path,
  no API payloads.
- No feature flag: flagging would keep the broken button as the fallback
  state, which is strictly worse than either version; the OTA republish path
  above is the flag-equivalent single-step revert.

## 9. Verification performed

- [x] `npx tsc --noEmit` on `rider-app` — clean.
- [x] Production JS bundle (`npx expo export --platform android`) run to
  verify the app bundles with the change — this is the closest rider-app
  equivalent of a production build (result recorded in the PR/commit; see
  "not verified" if it could not complete in the session environment).
- [x] Blast-radius grep: `styles.modalOverlay} activeOpacity={1}` (whole
  rider-app — only this file), `<Modal` in `ride-options.tsx` (only this
  modal), importers of the modal (none — inline).
- [x] Reviewed against `CLAUDE.md` conventions: no money arithmetic, no state
  machine, no API contract change; UX-effect field filled in (§5).
- [ ] Automated tests: **none run for this screen** — no render harness
  exists for `ride-options.tsx` (unmocked `react-native-maps`,
  `@gorhom/bottom-sheet`, `MapViewDirections`); building one was out of
  scope for this hotfix. Standing gap noted below.
- [ ] Feature flag: not used — justified in §8.

## 10. What was NOT verified

- **On-device touch behavior** — this session has no device/emulator; the
  fix was not physically tapped on Android/iOS. It must be smoke-tested on
  the tester's device after the OTA/update: vehicle selection → Payment →
  select wallet → Done closes the sheet → row shows "Wallet · $<balance>".
- The root-cause mechanism (New-Arch nested-touchable press loss) was
  diagnosed from code structure + symptom fit, not reproduced/bisected.
- No visual regression tooling exists for rider-app (standing gap, see
  `ACTION_ITEMS.md`) — pixel-identity of the sheet was reasoned (same
  styles), not screenshotted.
- `expo lint` / eslint could not complete: pre-existing
  `eslint-plugin-react` internal crash in this environment, unrelated to
  this diff.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (OTA republish of prior update group)
- [x] Blast radius is stated, not assumed (isolated; greps listed)
- [x] No silent behavior change: the only shipped-flow change is the broken
  button starting to work; §5 filled in
