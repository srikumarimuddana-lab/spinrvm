/**
 * Regression tests for the booking-screen payment selector.
 *
 * Bug: a new customer with no card/wallet who adds their first card on
 * /manage-cards returned to a booking screen whose payment selector was still
 * empty (mount-only fetch never re-ran on focus). The focus refetch now feeds
 * the card list through selectDefaultCardId, which must auto-select the newly
 * added default card while preserving any explicit selection the rider made.
 */

import { selectDefaultCardId } from '../selectDefaultCard';

describe('selectDefaultCardId', () => {
  it('auto-selects the freshly added first card (backend marks it default)', () => {
    // New customer: previous selection is null, one card now exists.
    expect(selectDefaultCardId(null, [{ id: 'pm_new', is_default: true }])).toBe('pm_new');
  });

  it('returns null when there are still no cards', () => {
    expect(selectDefaultCardId(null, [])).toBeNull();
  });

  it('preserves the rider explicit selection when that card still exists', () => {
    const cards = [
      { id: 'pm_default', is_default: true },
      { id: 'pm_chosen', is_default: false },
    ];
    expect(selectDefaultCardId('pm_chosen', cards)).toBe('pm_chosen');
  });

  it('falls back to the default card when the previous selection was removed', () => {
    const cards = [
      { id: 'pm_a', is_default: false },
      { id: 'pm_default', is_default: true },
    ];
    expect(selectDefaultCardId('pm_gone', cards)).toBe('pm_default');
  });

  it('falls back to the first card when none is flagged default', () => {
    const cards = [{ id: 'pm_first' }, { id: 'pm_second' }];
    expect(selectDefaultCardId(null, cards)).toBe('pm_first');
  });
});
