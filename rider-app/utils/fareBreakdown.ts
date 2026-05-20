export interface FareBreakdownInput {
  totalAmount: number;
  fareAmount: number;
  rideFare: number;
  tipAmount: number;
  discountAmount: number;
  promoCode: string;
  surgeMultiplier: number;
}

export interface FareBreakdownResult {
  total: number;
  rideFare: number;
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
  const { totalAmount, fareAmount, rideFare, tipAmount, discountAmount, promoCode, surgeMultiplier } = input;
  const total = Math.abs(totalAmount);
  const fareAfterDiscount = fareAmount > 0 ? fareAmount : total - tipAmount;

  return {
    total,
    rideFare,
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
  base_fare?: number | string;
  distance_fare?: number | string;
  time_fare?: number | string;
  tip_amount?: number | string;
  discount_amount?: number | string;
  promo_code?: string;
  surge_multiplier?: number | string;
}): FareBreakdownResult {
  if (ride.status === 'cancelled') {
    return {
      total: 0, rideFare: 0, fareAfterDiscount: 0,
      tip: 0, discount: 0, promoCode: '', surge: 1,
      hasTip: false, hasDiscount: false, hasSurge: false,
    };
  }

  const grandTotal = parseFloat(String(ride.grand_total ?? ride.total_fare ?? 0)) || 0;
  const tip = parseFloat(String(ride.tip_amount ?? 0)) || 0;
  const discount = parseFloat(String(ride.discount_amount ?? 0)) || 0;
  const surge = parseFloat(String(ride.surge_multiplier ?? 1)) || 1;
  const baseFare = parseFloat(String(ride.base_fare ?? 0)) || 0;
  const distFare = parseFloat(String(ride.distance_fare ?? 0)) || 0;
  const timeFare = parseFloat(String(ride.time_fare ?? 0)) || 0;
  const rideFare = baseFare + distFare + timeFare;
  const total = grandTotal + tip;

  return {
    total,
    rideFare,
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
