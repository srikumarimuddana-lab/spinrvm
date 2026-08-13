# Change Impact & Risk Log — Payment selector rebuilt as a @gorhom bottom sheet (RN Modal removed)

## Summary

Fourth round on the payment-sheet touch bug, and the first with a decisive
on-device falsification of the previous fix: the rider received the round-3
select-to-dismiss OTA on TestFlight 2.0.0 (16) (the Done button is gone,
proving the bundle applied) and reports the sheet **still does not close
when tapping a payment method**. The round-3 jest contract proves the row
handlers both select and dismiss when they fire — so the presses are dying
in the native layer, not in JS. Conclusion: the entire RN `Modal` stacked
over the ride-options @gorhom bottom sheet is touch-dead on iOS New
Architecture, not just the strip below its ScrollView. Remediation: remove
the Modal entirely and render the payment selector as a third
`@gorhom/bottom-sheet` instance — the same library as the vehicle sheet and
the promo sheet, the one overlay pattern proven to receive touches on this
screen on-device.

## 1. Issue / gap identified

Tapping a payment row (Spinr Wallet / saved card / corporate account) in the
payment selector does not apply-and-dismiss on iOS TestFlight 2.0.0 (16),
even on the round-3 bundle where rows are wired to do exactly that.

## 2. Root cause

An RN `Modal` presented over a @gorhom bottom sheet does not receive touches
on iOS under the New Architecture — it renders correctly but presses never
reach its children. Rounds 1–3 fixed real JS-layer issues (nested
Touchables, OTA delivery, footer-in-dead-zone) but kept the Modal container;
the container itself is the operative failure.

## 3. Fix / remediation

- The payment selector's RN `Modal` (backdrop `Pressable` + styled `View`
  sheet + `ScrollView`) is replaced by a `BottomSheet` +
  `BottomSheetScrollView` sibling instance mirroring the promo sheet's
  conventions: `index={-1}`, `enableDynamicSizing` capped at 80% screen
  height, `enablePanDownToClose`, `BottomSheetBackdrop` with
  `pressBehavior="close"`.
- `showPaymentSheet` boolean state is replaced by imperative ref control
  (`openPaymentSheet`/`closePaymentSheet`), matching `openPromoSheet`.
- Row onPress bodies are unchanged except `setShowPaymentSheet(false)` →
  `closePaymentSheet()`; selection semantics and `createRide` inputs are
  untouched.
- The old Modal's `onRequestClose` (Android hardware back) is preserved via
  an explicit `BackHandler` effect active only while the sheet is open.
- Unused `Modal`, `ScrollView`, `Pressable` imports and the
  `modalOverlay`/`paymentModal`/`paymentModalHandle` styles are removed;
  `paymentSheetContent` added (mirrors `promoSheetContent`).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `rider-app/app/ride-options.tsx`.** The new
  plumbing names (`paymentSheetRef`, `openPaymentSheet`, `closePaymentSheet`,
  `renderPaymentBackdrop`, `paymentSheetContent`) are screen-local; grep
  shows no other file references them or the removed
  `showPaymentSheet`/`modalOverlay`/`paymentModal` names.
- Payment/booking logic untouched: `selectedPayment`, `selectedCardId`,
  `useCorporate`, `selectedCorporateId` state and every reader are
  unchanged.
- Both open-triggers converted (footer Payment row; ConfirmSheet
  "Add / select card" button). No other `setShowPaymentSheet` callers
  existed.
- z-order: the payment sheet is declared after the vehicle sheet and before
  the promo sheet; payment and promo sheets are never open simultaneously,
  so sibling stacking order cannot conflict.
- Android regression surface: Android testers had a *working* Modal. The
  bottom sheet changes the presentation (pan-down handle instead of a slide
  Modal) and hardware-back is explicitly re-wired; behavior parity was
  reasoned through, not device-tested.
- **Same-class risk flagged, not fixed here:** `SchedulePicker` and
  `ConfirmSheet` are separate components that may also present RN Modals
  over this screen's bottom sheet. If they are Modal-based they likely share
  the iOS New-Arch touch-dead failure. Out of scope for this single-change
  commit; needs its own pass.

## 5. User experience effect

Rider-facing, visible mid-session after OTA: the payment selector now rises
as a standard bottom sheet (drag handle, pan-down-to-close) instead of a
slide-up modal. Tapping a method applies it and closes the sheet — on iOS
this is the first build where that interaction can physically work.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `rider-app/app/ride-options.tsx` | Payment `Modal` → third `@gorhom/bottom-sheet` instance; ref-based open/close; BackHandler effect; import/style cleanup | RN Modal over a @gorhom sheet is touch-dead on iOS New Arch |
| `rider-app/__tests__/ride-options-payment-sheet.test.tsx` | Contract rewritten: pins BottomSheet-not-Modal, select-to-dismiss rows, no footer control, backdrop close, hardware-back exit | Fails if the stacked-Modal pattern or a footer-only exit returns |
| `docs/change-log/2026-08-12-rider-payment-sheet-bottomsheet.md` | This entry | Live-testing change log requirement |

## 7. Before/after snippet

Before (round 3 — RN Modal stacked over the vehicle bottom sheet):

```tsx
<Modal visible={showPaymentSheet} animationType="slide" transparent
       onRequestClose={() => setShowPaymentSheet(false)}>
  <View style={styles.modalOverlay}>
    <Pressable style={StyleSheet.absoluteFill} onPress={() => setShowPaymentSheet(false)} />
    <View style={styles.paymentModal}>
      <ScrollView style={{ maxHeight: 400 }}>
        <TouchableOpacity onPress={() => { setSelectedPayment('wallet'); setUseCorporate(false); setShowPaymentSheet(false); }}>
```

After (round 4 — sibling @gorhom sheet, same library as the working sheets):

```tsx
<BottomSheet ref={paymentSheetRef} index={-1} enableDynamicSizing
             maxDynamicContentSize={paymentMaxSheetHeight} enablePanDownToClose
             backdropComponent={renderPaymentBackdrop} onChange={handlePaymentSheetChange}>
  <BottomSheetScrollView contentContainerStyle={styles.paymentSheetContent}>
    <TouchableOpacity onPress={() => { setSelectedPayment('wallet'); setUseCorporate(false); closePaymentSheet(); }}>
```

## 8. Rollback plan

JS-only, no data/native/store involvement: `git revert` the commit and
republish the OTA channels (push to main auto-publishes `preview`; dispatch
"EAS Mobile Update" with `rider-app`/`production` for TestFlight). Reverting
restores the RN Modal *including its iOS touch-dead bug*. `runtimeVersion`
stays 2.0.0 — @gorhom/bottom-sheet was already in the binary (vehicle +
promo sheets), so no native rebuild in either direction.

## 9. Verification performed

- `npx jest __tests__/ride-options-payment-sheet.test.tsx` — 7/7 pass on the
  rewritten contract.
- Full rider-app jest suite run (see PR for result).
- `npx tsc --noEmit` — clean.
- `npx expo export --platform ios` — **real production bundle build run**
  (not just dev server / tsc), per the live-testing gate.

## 10. What was NOT verified

- No iOS device/simulator in this environment: the fix is built on the
  promo/vehicle sheets' proven on-device touch behavior, but the payment
  sheet itself was not tapped on an iPhone. The reporter's TestFlight retest
  is the confirming step.
- Android behavior parity (hardware back, pan-down feel) reasoned about,
  not device-tested.
- No visual regression tooling exists in this repo (standing gap,
  ACTION_ITEMS.md) — the Modal→sheet presentation change was not
  screenshot-compared.
- Whether `SchedulePicker`/`ConfirmSheet` share the stacked-Modal bug was
  not investigated (flagged in §4).
