import fs from 'fs';
import path from 'path';

const source = fs.readFileSync(
  path.resolve(__dirname, '..', 'app', 'payment-confirm.tsx'),
  'utf8',
);

// Ranked audit blocker #23 (docs/audit/2026-08-18-full-fleet-whole-app-audit.md,
// baseline #17 / finding N7): the /payments/cards fetch failure rendered
// identically to "you genuinely have no cards on file" — the catch block
// only console.warn'd and left savedCards as [], so a rider whose cards
// failed to load saw the exact same "Tap to add a card" prompt as a rider
// who really has none. This contract pins the fix: a distinct error state
// (cardsLoadError) with its own copy and a retry action, gated so the two
// states can never render at once.
describe('payment-confirm: fetch-failure vs genuine-empty-cards distinction', () => {
  it('tracks a dedicated cardsLoadError flag, separate from savedCards.length', () => {
    expect(source).toContain('const [cardsLoadError, setCardsLoadError] = useState(false);');
  });

  it('sets the error flag on fetch failure and clears it on success', () => {
    const loadFnMatch = source.match(
      /const loadSavedCards = useCallback\(\(\) => \{[\s\S]*?\}, \[\]\);/,
    );
    expect(loadFnMatch).not.toBeNull();
    const loadFn = loadFnMatch![0];
    // success path clears any prior error
    expect(loadFn).toMatch(/setCardsLoadError\(false\);/);
    // catch path sets it, and still surfaces the underlying error loudly
    // (console.warn kept — not silently swallowed into a generic fallback)
    expect(loadFn).toMatch(/\.catch\(\(e\) => \{[\s\S]*console\.warn\([\s\S]*setCardsLoadError\(true\);[\s\S]*\}\);/);
  });

  it('renders a distinct error row with retry copy, not the empty-state copy', () => {
    expect(source).toContain('Couldn&apos;t load your cards');
    expect(source).toContain('Tap to retry');
    // Retries by re-invoking the same loader (no separate, divergent retry path)
    expect(source).toMatch(/cardsLoadError &&[\s\S]{0,200}onPress=\{loadSavedCards\}/);
  });

  it('keeps the genuine-empty-state row for the case with no error', () => {
    expect(source).toContain('Tap to add a card');
    // Guarded so the empty-state "add a card" row cannot render while an
    // error is active — the two states are mutually exclusive.
    expect(source).toMatch(/!cardsLoadError && savedCards\.length === 0 &&/);
  });

  it('the error row and empty-state row use different accessibility labels', () => {
    expect(source).toContain("Couldn't load your payment methods, tap to retry");
    expect(source).toContain('accessibilityLabel="Add a credit card"');
  });
});
