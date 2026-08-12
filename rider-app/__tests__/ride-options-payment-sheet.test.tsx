import fs from 'fs';
import path from 'path';

const source = fs.readFileSync(
  path.resolve(__dirname, '..', 'app', 'ride-options.tsx'),
  'utf8',
);

// Live-testing bug (2026-08-11): the payment-method sheet's footer Done
// button was dead on iOS. First diagnosis (nested Touchable wrappers, fixed
// in 5f18a92) proved insufficient: on TestFlight 2.0.0 (16), with the flat
// structure embedded, the button still never received touches. The operative
// cause is the stacked-sheet layout — this RN Modal slides over the
// ride-options bottom sheet, and on the New Architecture the modal region
// below the ScrollView is a touch dead zone. The footer button was removed
// entirely: rows now commit their selection and dismiss in one press
// (matching the promo sheet, which closes on apply by design).
// This contract pins that shape against reintroduction of either failure:
// a Touchable ancestor above the rows, or any footer control that would sit
// in the dead zone as the sheet's only exit.
// Full log: docs/change-log/2026-08-11-rider-payment-sheet-done-button.md
describe('payment-method sheet touch-delivery contract', () => {
  // Everything between the modal's opening tag and the promo sheet that
  // follows it — scoping the assertions so unrelated screens/sheets in this
  // large file can't satisfy (or trip) them by accident.
  const modalSource = source.slice(
    source.indexOf('Payment method modal'),
    source.indexOf('Promo selection sheet'),
  );

  it('locates the payment modal region in ride-options.tsx', () => {
    expect(modalSource.length).toBeGreaterThan(0);
    expect(modalSource).toContain('visible={showPaymentSheet}');
  });

  it('keeps the backdrop a sibling Pressable, never a Touchable parent of the sheet', () => {
    expect(modalSource).toContain(
      '<Pressable style={StyleSheet.absoluteFill} onPress={() => setShowPaymentSheet(false)} />',
    );
    // The original backdrop bug: a TouchableOpacity styled as the overlay
    // that *wrapped* the sheet. Any Touchable carrying the overlay style
    // means the parent-wrapper pattern is back.
    expect(modalSource).not.toMatch(
      /<(TouchableOpacity|TouchableWithoutFeedback|Pressable)[\s\S]{0,200}?style=\{styles\.modalOverlay\}/,
    );
  });

  it('keeps the sheet container a plain View with no Touchable ancestors above the rows', () => {
    expect(modalSource).toContain('<View style={styles.paymentModal}>');
    expect(modalSource).not.toMatch(
      /<(TouchableOpacity|TouchableWithoutFeedback)[\s\S]{0,200}?style=\{styles\.paymentModal\}/,
    );
  });

  it('has no footer button — the dead zone below the ScrollView must hold no control', () => {
    // On iOS (New Arch) the modal region below the ScrollView never receives
    // touches when this Modal is stacked over the ride-options bottom sheet;
    // TestFlight 2.0.0 (16) proved a footer button there renders but is
    // un-tappable. Nothing interactive may live between </ScrollView> and
    // the sheet's closing </View>.
    expect(modalSource).not.toContain('paymentDoneBtn');
    const afterScroll = modalSource.slice(modalSource.indexOf('</ScrollView>'));
    expect(afterScroll).not.toMatch(/<(TouchableOpacity|Pressable|Button)\b/);
  });

  it('dismisses the sheet in the same press that selects a payment method', () => {
    // Every selectable row must both commit its selection and close the
    // sheet — with no Done button, a row that only selects would strand the
    // rider (backdrop tap aside). Match each row's onPress body.
    const rowPresses = [
      // saved card
      /setSelectedPayment\('card'\); setSelectedCardId\(card\.id\); setUseCorporate\(false\); setShowPaymentSheet\(false\);/,
      // wallet
      /setSelectedPayment\('wallet'\); setUseCorporate\(false\); setShowPaymentSheet\(false\);/,
      // corporate account
      /setUseCorporate\(true\); setSelectedCorporateId\(acct\.id\); setShowPaymentSheet\(false\);/,
    ];
    for (const press of rowPresses) {
      expect(modalSource).toMatch(press);
    }
  });

  it('keeps the add-payment escape hatches that close the sheet before navigating', () => {
    // "Add payment method" row (always) and the empty-state card row must
    // close the sheet and route to manage-cards.
    const navPresses = modalSource.match(
      /setShowPaymentSheet\(false\); router\.push\('\/manage-cards' as any\);/g,
    );
    expect(navPresses?.length).toBe(2);
    expect(modalSource).toContain('Add payment method');
  });

  it('keeps tap-outside-to-close and the Android back handler', () => {
    expect(modalSource).toContain('onRequestClose={() => setShowPaymentSheet(false)}');
  });
});
