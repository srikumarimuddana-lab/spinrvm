import fs from 'fs';
import path from 'path';

const source = fs.readFileSync(
  path.resolve(__dirname, '..', 'app', 'ride-options.tsx'),
  'utf8',
);

// Live-testing bug (2026-08-11): the payment-method sheet's Done button was
// dead because it sat under two parent TouchableOpacity wrappers (backdrop +
// sheet), a nesting that drops child presses on the New Architecture. Rows
// inside the ScrollView kept working via the scroll responder, masking it.
// Fixed in 5f18a92 by making the backdrop a sibling Pressable under a plain
// View sheet. The same nesting killed the language-picker rows (2e4826b), so
// this contract pins the structure against a third reintroduction.
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
    // The pre-fix backdrop: a TouchableOpacity styled as the overlay that
    // *wrapped* the sheet. Any Touchable carrying the overlay style means the
    // parent-wrapper pattern is back.
    expect(modalSource).not.toMatch(
      /<(TouchableOpacity|TouchableWithoutFeedback|Pressable)[\s\S]{0,200}?style=\{styles\.modalOverlay\}/,
    );
  });

  it('keeps the sheet container a plain View so Done has no Touchable ancestors', () => {
    expect(modalSource).toContain('<View style={styles.paymentModal}>');
    expect(modalSource).not.toMatch(
      /<(TouchableOpacity|TouchableWithoutFeedback)[\s\S]{0,200}?style=\{styles\.paymentModal\}/,
    );
  });

  it('keeps the Done button wired to close the sheet', () => {
    expect(modalSource).toContain(
      '<TouchableOpacity style={styles.paymentDoneBtn} onPress={() => setShowPaymentSheet(false)}>',
    );
  });

  it('keeps tap-outside-to-close and the Android back handler', () => {
    expect(modalSource).toContain('onRequestClose={() => setShowPaymentSheet(false)}');
  });
});
