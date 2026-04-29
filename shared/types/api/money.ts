/** Decimal money value serialized as a 2-dp string (e.g. "12.50"). Never parse with parseFloat for arithmetic. */
export type MoneyString = string & { readonly __brand: 'MoneyString' };

/** Parse a MoneyString for DISPLAY only. Never use for arithmetic. */
export const moneyToDisplay = (v: MoneyString | string): string =>
  parseFloat(v).toFixed(2);

export type TransactionType =
  | 'top_up'
  | 'ride_payment'
  | 'ride_refund'
  | 'bonus'
  | 'referral'
  | 'cashout'
  | 'fare_split_received'
  | 'fare_split_sent'
  | 'quest_reward';

export interface WalletBalance {
  id: string;
  user_id: string;
  balance: MoneyString;
  currency: string;
  created_at: string;
}

export interface WalletTransaction {
  id: string;
  wallet_id: string;
  type: TransactionType;
  amount: MoneyString;
  balance_after: MoneyString;
  reference_id?: string;
  description?: string;
  metadata?: Record<string, unknown>;
  created_at: string;
}

export interface FareEstimate {
  base_fare: MoneyString;
  distance_fare: MoneyString;
  time_fare: MoneyString;
  booking_fee: MoneyString;
  /** Numeric multiplier, not money. */
  surge_multiplier: number;
  total_fare: MoneyString;
}

export interface Receipt {
  base_fare: MoneyString;
  distance_fare: MoneyString;
  time_fare: MoneyString;
  airport_fee: MoneyString;
  booking_fee: MoneyString;
  tax_amount: MoneyString;
  tip_amount: MoneyString;
  total_charged: MoneyString;
}
