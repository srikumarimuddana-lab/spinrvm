export interface FareBreakdownInput {
  totalAmount: number;
  fareAmount: number;
  tipAmount: number;
  discountAmount: number;
  promoCode: string;
  surgeMultiplier: number;
}

export interface FareBreakdownResult {
  total: number;
  originalFare: number;
  fareAfterDiscount: number;
  tip: number;
  discount: number;
  promoCode: string;
  surge: number;
  hasTip: boolean;
  hasDiscount: boolean;
  hasSurge: boolean;
}

export function computeFareBreakdown(input: FareBreakdownInput): FareBreakdownResult {
  const { totalAmount, fareAmount, tipAmount, discountAmount, promoCode, surgeMultiplier } = input;
  const fareAfterDiscount = tipAmount > 0 ? Math.abs(totalAmount) - tipAmount : fareAmount;
  const originalFare = discountAmount > 0 ? fareAfterDiscount + discountAmount : fareAfterDiscount;

  return {
    total: Math.abs(totalAmount),
    originalFare,
    fareAfterDiscount,
    tip: tipAmount,
    discount: discountAmount,
    promoCode,
    surge: surgeMultiplier,
    hasTip: tipAmount > 0,
    hasDiscount: discountAmount > 0,
    hasSurge: surgeMultiplier > 1,
  };
}

export function computeFareFromRide(ride: {
  status?: string;
  grand_total?: number | string;
  total_fare?: number | string;
  tip_amount?: number | string;
  discount_amount?: number | string;
  promo_code?: string;
  surge_multiplier?: number | string;
}): FareBreakdownResult {
  if (ride.status === 'cancelled') {
    return {
      total: 0, originalFare: 0, fareAfterDiscount: 0,
      tip: 0, discount: 0, promoCode: '', surge: 1,
      hasTip: false, hasDiscount: false, hasSurge: false,
    };
  }

  const grandTotal = parseFloat(String(ride.grand_total ?? ride.total_fare ?? 0)) || 0;
  const tip = parseFloat(String(ride.tip_amount ?? 0)) || 0;
  const discount = parseFloat(String(ride.discount_amount ?? 0)) || 0;
  const surge = parseFloat(String(ride.surge_multiplier ?? 1)) || 1;
  const total = grandTotal + tip;
  const originalFare = discount > 0 ? grandTotal + discount : grandTotal;

  return {
    total,
    originalFare,
    fareAfterDiscount: grandTotal,
    tip,
    discount,
    promoCode: ride.promo_code || '',
    surge,
    hasTip: tip > 0,
    hasDiscount: discount > 0,
    hasSurge: surge > 1,
  };
}
