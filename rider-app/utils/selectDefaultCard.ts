export interface SavedCardLike {
  id: string;
  is_default?: boolean;
}

/**
 * Decide which saved card should be selected after (re)loading the card list.
 *
 * Used by the booking screens (ride-options, payment-confirm) when the saved
 * cards are refetched — notably when the screen regains focus after the rider
 * adds a card on /manage-cards. The rules:
 *
 *   1. Keep the rider's explicit selection if that card still exists — never
 *      clobber a deliberate choice on a background refetch.
 *   2. Otherwise fall back to the default card. The backend marks the first
 *      card a customer adds as default, so a brand-new customer's first card
 *      is auto-selected and they can proceed to confirm their ride.
 *   3. Otherwise fall back to the first card in the list.
 *   4. No cards → null.
 */
export function selectDefaultCardId(
  previousSelectedId: string | null,
  cards: SavedCardLike[],
): string | null {
  if (previousSelectedId && cards.some((c) => c.id === previousSelectedId)) {
    return previousSelectedId;
  }
  const fallback = cards.find((c) => c.is_default) ?? cards[0];
  return fallback?.id ?? null;
}
