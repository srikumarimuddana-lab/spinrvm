export interface PromoLike {
  free_ride?: boolean;
  discount_type?: string; // 'flat' | 'percentage'
  discount_value?: number | string;
  max_discount?: number | string | null;
  min_ride_fare?: number | string | null;
}

export interface EstimateFareLike {
  grand_total?: number | string;
  total_fare?: number | string;
  base_fare?: number | string;
  distance_fare?: number | string;
  time_fare?: number | string;
}

const num = (v: unknown): number => parseFloat(String(v ?? 0)) || 0;
const round2 = (n: number): number => Math.round(n * 100) / 100;

/** Ride portion = base + distance + time. Promos never discount fees/taxes. */
export function ridePortionOf(estimate: EstimateFareLike): number {
  return num(estimate.base_fare) + num(estimate.distance_fare) + num(estimate.time_fare);
}

export function grandTotalOf(estimate: EstimateFareLike): number {
  return num(estimate.grand_total ?? estimate.total_fare);
}

/**
 * Display-side mirror of the server's promo math
 * (backend/routes/promotions.py). The server recomputes and enforces the
 * discount at booking, so this can never change what's charged — it exists
 * so every vehicle card can show its own discounted price. The
 * server-computed `discount_amount` is only valid for the single fare the
 * promo list was fetched with, and goes stale the moment the rider switches
 * vehicle type.
 *
 *  - free_ride: covers the card's entire grand total
 *  - percentage: % of the RIDE PORTION (base+distance+time — never fees or
 *    taxes), capped at max_discount
 *  - flat: min(discount_value, ride portion)
 *  - min_ride_fare eligibility is checked against the ride portion
 *
 * Returns 0 when the promo doesn't apply to this fare.
 */
export function promoDiscountForEstimate(
  promo: PromoLike | null | undefined,
  estimate: EstimateFareLike | null | undefined,
): number {
  if (!promo || !estimate) return 0;

  const ridePortion = ridePortionOf(estimate);
  const minFare = num(promo.min_ride_fare);
  if (minFare > 0 && ridePortion < minFare) return 0;

  if (promo.free_ride) return round2(grandTotalOf(estimate));

  const value = num(promo.discount_value);
  if (promo.discount_type === 'percentage') {
    let discount = (ridePortion * value) / 100;
    const cap = num(promo.max_discount);
    if (cap > 0 && discount > cap) discount = cap;
    return round2(discount);
  }
  // flat
  return round2(Math.min(value, ridePortion));
}
