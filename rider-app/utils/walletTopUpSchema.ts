import { z } from 'zod';

/**
 * app/wallet.tsx — wallet top-up amount validation.
 * Pin: matches the screen's pre-existing inline check byte-for-byte
 * (`effectiveAmount >= 1 && effectiveAmount <= 500`) — a pure extraction,
 * not a validation-rule change. B39 (ACTION_ITEMS.md) step 2: highest-risk
 * money-adjacent form after work-allowance-request.tsx (step 1), since this
 * amount feeds a real Stripe PaymentSheet top-up.
 */
export const walletTopUpSchema = z.number().min(1).max(500);

export function isWalletTopUpAmountValid(amount: number): boolean {
  return walletTopUpSchema.safeParse(amount).success;
}
