# Change Impact & Risk Log — Payment sheet: select-to-dismiss, Done button removed

## Summary

Third round on the dead payment-sheet Done button. The structural fix
(5f18a92, flat Pressable/View hierarchy) shipped embedded in TestFlight
rider iOS 2.0.0 (16) — and the reporter confirmed the button is **still**
un-tappable on that build. That falsifies the nested-Touchable diagnosis as
the complete cause: the operative failure is the stacked-sheet layout. The
payment selector is an RN `Modal` sliding over the ride-options bottom
sheet, and on iOS (New Architecture) the modal region below the ScrollView
never receives touches, so any footer control there renders but is dead.
Remediation (also the product owner's requested UX): remove the footer
Done button entirely; tapping a payment row now commits the selection and
dismisses the sheet in the same press. "Add payment method" stays.

## 1. Issue / gap identified

The payment-method sheet's footer Done button does not respond to taps on
iOS even with the flat (no nested Touchables) structure — confirmed on
TestFlight 2.0.0 (16), whose embedded bundle contains fix 5f18a92. Riders
who open the sheet can only escape via backdrop tap.

## 2. Root cause

Sheet-over-sheet stacking, not (only) Touchable nesting. `ride-options`
renders a @gorhom bottom sheet; the payment selector is a transparent RN
`Modal` presented on top of it. On the New Architecture on iOS, touches in
the modal's region below the ScrollView are not delivered — rows inside the
ScrollView work (scroll responder path), while the footer area is a dead
zone. The 5f18a92 structural fix was necessary hygiene but insufficient:
it changed the button's ancestors, not the dead zone it sat in.

## 3. Fix / remediation

- Every selectable row (saved card, Spinr Wallet, corporate account) now
  appends `setShowPaymentSheet(false)` to its onPress — selection and
  dismissal are one press. This matches the promo sheet on the same screen,
  which already closes on apply "by design".
- The footer Done `TouchableOpacity` and its `paymentDoneBtn` /
  `paymentDoneBtnText` styles are deleted. Nothing interactive remains
  below the ScrollView.
- "Add payment method" and the empty-state "Credit Card — tap to add" rows
  keep their existing close-then-navigate behavior (unchanged).
- Backdrop tap and `onRequestClose` (Android back) remain as exits.

## 4. Risk & impact on existing functionality

- Blast radius: `rider-app/app/ride-options.tsx` only. The removed styles
  are referenced nowhere else (grep: `paymentDoneBtn` had exactly the two
  in-file uses). `showPaymentSheet` state is local to this screen. No other
  screen imports this modal; the driver-app earnings screen's similar
  layout is viewer-only (no selector).
- Selection semantics are unchanged: rows always committed live state
  (`selectedPayment` / `selectedCardId` / `useCorporate` /
  `selectedCorporateId`) on tap — Done was purely a closer. No payment
  routing, fare, or booking logic is touched; `createRide` reads the same
  state it always did.
- Behavior loss: a rider can no longer keep the sheet open to visually
  compare options after selecting — each tap closes it. Reopening is one
  tap on the payment row, so accepted.
- The dead zone itself is not fixed — it is now empty. If a future change
  puts any control below the ScrollView, it will be dead on iOS again; the
  regression test now fails on exactly that.

## 5. User-experience effect

Rider-facing, visible mid-session on a live-tested surface: the payment
sheet loses its Done button and closes immediately on selecting a payment
method (Uber-style). Riders mid-ride-booking see the new behavior on next
bundle load. This is the product owner's explicitly requested UX, not a
side effect.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `rider-app/app/ride-options.tsx` | Rows dismiss on select; footer Done button + styles removed; comment rewritten | Footer sits in an iOS touch dead zone; select-to-dismiss removes the need for it |
| `rider-app/__tests__/ride-options-payment-sheet.test.tsx` | Contract rewritten: no footer control below ScrollView, every row press also dismisses, add-payment escape hatches intact | Old contract pinned the Done button that build 16 proved dead |

## 7. Before / after

Before (rows only select; the only in-sheet exit is a footer button that is
un-tappable on iOS):

```tsx
onPress={() => { setSelectedPayment('wallet'); setUseCorporate(false); }}>
...
</ScrollView>
<TouchableOpacity style={styles.paymentDoneBtn} onPress={() => setShowPaymentSheet(false)}>
  <Text style={styles.paymentDoneBtnText}>Done</Text>
</TouchableOpacity>
```

After (one press selects and dismisses; nothing below the ScrollView):

```tsx
onPress={() => { setSelectedPayment('wallet'); setUseCorporate(false); setShowPaymentSheet(false); }}>
...
</ScrollView>
```

## 8. Rollback plan

JS-only, no data or schema touched. Revert this commit and publish an OTA
to the same channels (`eas-build.yml` dispatch); no native build, store
review, or migration involved. Reverting restores the Done button —
including its iOS dead-zone bug — so rollback is only sensible if
select-to-dismiss itself misbehaves.

## 9. Verification performed

- `npx jest __tests__/ride-options-payment-sheet.test.tsx` — 7/7 pass.
- `npx tsc --noEmit` — clean.
- `npx expo export --platform ios` production bundle — see PR thread for
  result (run started with this commit; a dev server alone was NOT relied
  on).

## 10. What was NOT verified

- **On-device iOS confirmation** — no simulator/device here. The decisive
  check is the reporter retesting on TestFlight after the next
  production-channel OTA (or build 17): tap Spinr Wallet → sheet closes,
  payment row shows Wallet.
- The dead-zone mechanism is inferred from strong on-device evidence
  (flat-structure button dead on build 16; ScrollView rows alive), not from
  an isolated repro. If rows are ALSO dead on iOS — not just Done — this
  fix is insufficient and the Modal must be replaced with a second @gorhom
  sheet or the selector inlined into the main sheet; the reporter's retest
  distinguishes the two.
- No visual regression tooling exists for rider-app (standing gap); layout
  after footer removal was reasoned about, not screenshotted.
