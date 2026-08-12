import fs from 'fs';
import path from 'path';

const source = fs.readFileSync(
  path.resolve(__dirname, '..', 'app', 'ride-options.tsx'),
  'utf8',
);

// Live-testing bug, round 4 (2026-08-12): the payment selector was an RN
// <Modal> sliding over the ride-options @gorhom bottom sheet. On iOS New
// Architecture (TestFlight 2.0.0 (16)) that stacked Modal was touch-dead:
// first the footer Done button (round 3 removed it in favor of
// select-to-dismiss rows), then the rider confirmed the rows themselves never
// received presses either — the sheet could not be dismissed by tapping a
// payment method. The jest-verified handlers were correct, so the presses
// were dying in the native layer: the Modal-over-sheet stack itself.
// The fix replaces the Modal with a third @gorhom/bottom-sheet instance —
// the same library as the vehicle and promo sheets, the one overlay pattern
// proven to receive touches on this screen on-device.
// This contract pins that shape against reintroduction of either failure:
// an RN Modal stacked for payment selection, or any footer control that
// would again be the sheet's only exit.
// Full log: docs/change-log/2026-08-12-rider-payment-sheet-bottomsheet.md
describe('payment-method sheet touch-delivery contract', () => {
  // Everything between the payment sheet's banner comment and the promo
  // sheet that follows it — scoping the assertions so unrelated sheets in
  // this large file can't satisfy (or trip) them by accident.
  const sheetSource = source.slice(
    source.indexOf('Payment method sheet'),
    source.indexOf('Promo selection sheet'),
  );

  it('locates the payment sheet region in ride-options.tsx', () => {
    expect(sheetSource.length).toBeGreaterThan(0);
    expect(sheetSource).toContain('ref={paymentSheetRef}');
  });

  it('is a @gorhom bottom sheet, never an RN Modal stacked over the vehicle sheet', () => {
    // The operative iOS New-Arch failure: an RN <Modal> rendered over the
    // @gorhom vehicle sheet draws but does not receive touches. The selector
    // must be a sibling BottomSheet from the same library instead.
    expect(sheetSource).toMatch(/^\s*<BottomSheet\b/m);
    expect(sheetSource).toContain('<BottomSheetScrollView');
    expect(sheetSource).not.toMatch(/^\s*<Modal\b/m);
    // The Modal import must be gone from this screen entirely (SchedulePicker
    // and ConfirmSheet are separate components with their own files) — a
    // re-added import is the first sign of the stacked-Modal pattern coming
    // back.
    expect(source).not.toMatch(/^\s*Modal,$/m);
    expect(source).not.toContain('showPaymentSheet');
  });

  it('dismisses the sheet in the same press that selects a payment method', () => {
    // Every selectable row must both commit its selection and close the
    // sheet — there is no Done button, so a row that only selects would
    // strand the rider (backdrop tap aside). Match each row's onPress body.
    const rowPresses = [
      // saved card
      /setSelectedPayment\('card'\); setSelectedCardId\(card\.id\); setUseCorporate\(false\); closePaymentSheet\(\);/,
      // wallet
      /setSelectedPayment\('wallet'\); setUseCorporate\(false\); closePaymentSheet\(\);/,
      // corporate account
      /setUseCorporate\(true\); setSelectedCorporateId\(acct\.id\); closePaymentSheet\(\);/,
    ];
    for (const press of rowPresses) {
      expect(sheetSource).toMatch(press);
    }
  });

  it('keeps the add-payment escape hatches that close the sheet before navigating', () => {
    // "Add payment method" row (always) and the empty-state card row must
    // close the sheet and route to manage-cards.
    const navPresses = sheetSource.match(
      /closePaymentSheet\(\); router\.push\('\/manage-cards' as any\);/g,
    );
    expect(navPresses?.length).toBe(2);
    expect(sheetSource).toContain('Add payment method');
  });

  it('holds no control below the scroll view — rows are the exits, not a footer', () => {
    // Round 3's lesson: a footer control below the scrolling list was
    // un-tappable on-device and became a trap as the sheet's primary exit.
    // Nothing interactive may live between </BottomSheetScrollView> and the
    // sheet's close.
    expect(sheetSource).not.toContain('paymentDoneBtn');
    const afterScroll = sheetSource.slice(sheetSource.indexOf('</BottomSheetScrollView>'));
    expect(afterScroll).not.toMatch(/<(TouchableOpacity|Pressable|Button)\b/);
  });

  it('keeps tap-outside-to-close via the sheet backdrop', () => {
    // renderPaymentBackdrop lives with the sheet's plumbing above the JSX
    // region, so assert against the whole file.
    const backdrop = source.slice(
      source.indexOf('const renderPaymentBackdrop'),
      source.indexOf('), []);', source.indexOf('const renderPaymentBackdrop')),
    );
    expect(backdrop).toContain('pressBehavior="close"');
  });

  it('keeps the Android hardware-back exit the old Modal provided via onRequestClose', () => {
    const backHandler = source.slice(
      source.indexOf("BackHandler.addEventListener('hardwareBackPress'"),
      source.indexOf('paymentSheetOpen]'),
    );
    expect(backHandler).toContain('paymentSheetRef.current?.close()');
    expect(backHandler).toContain('return true');
  });
});
