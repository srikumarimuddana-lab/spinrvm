import { z } from "zod";

/**
 * dashboard/users/page.tsx's `requestWalletAction` -- validation extracted
 * from two inline checks per ACTION_ITEMS.md B39 (broader-sweep candidate:
 * admin-dashboard #1, money tier). Pure extraction, byte-for-byte
 * equivalent of the checks it replaces -- no behavior change.
 *
 * Distinct from `walletAdjustmentSchema.ts` (corporate-accounts wallet
 * adjustment): that form allows a signed delta (credit or debit via one
 * signed amount, up to $10,000, using `parseFloat`/`isNaN`) on a
 * corporate account's wallet. This form is the per-*user* (rider/driver)
 * wallet credit/debit dialog -- a separate `action: "credit" | "debit"`
 * plus an always-positive `amount` string, validated by a decimal-places
 * regex rather than `isNaN`, with its own reason-length floor (3 chars,
 * not just non-empty). Different field shapes, different call site
 * (`creditUserWallet`/`debitUserWallet`, not
 * `corporate-accounts/{id}/wallet/adjust`) -- kept as a separate schema
 * file rather than merged, per the established B39 pattern (see
 * `spinrPassAreaPlanSchema.ts` vs `subscriptionPlanSchema.ts`).
 *
 * Money-critical: `confirmWalletAction` posts straight to
 * `creditUserWallet`/`debitUserWallet` (per CLAUDE.md's Decimal-only
 * money-arithmetic rule -- backend converts to `Decimal`, this is the
 * client-side pre-submit gate only).
 */

// Same regex as the page's original inline check -- up to 2 decimal
// places, no leading sign (credit/debit direction is a separate action
// field, not encoded in the amount's sign). Kept byte-for-byte identical.
const WALLET_AMOUNT_REGEX = /^\d+(\.\d{1,2})?$/;

/**
 * Mirrors `requestWalletAction`'s
 * `!walletAmount || !WALLET_AMOUNT_REGEX.test(walletAmount.trim()) || parseFloat(walletAmount) <= 0`
 * check exactly (inverted to a positive predicate).
 */
export function isWalletAmountValid(walletAmount: string): boolean {
  if (!walletAmount) return false;
  const trimmed = walletAmount.trim();
  if (!WALLET_AMOUNT_REGEX.test(trimmed)) return false;
  return parseFloat(trimmed) > 0;
}

/**
 * Mirrors `requestWalletAction`'s
 * `!walletReason.trim() || walletReason.trim().length < 3` check exactly
 * (inverted to a positive predicate).
 */
export function isWalletReasonValid(walletReason: string): boolean {
  return walletReason.trim().length >= 3;
}

export const userWalletActionSchema = z.object({
  amount: z.string().refine(isWalletAmountValid, {
    message: "Enter a positive amount",
  }),
  reason: z.string().refine(isWalletReasonValid, {
    message: "Reason must be at least 3 characters",
  }),
});

export type UserWalletActionInput = z.infer<typeof userWalletActionSchema>;

/**
 * Runs the same checks as the page's old inline `requestWalletAction`, in
 * the same order (amount first, then reason), and returns the same error
 * string for the first failing check -- or null if both pass. Drop-in
 * replacement for the two sequential `if` blocks + `setWalletError(...)`
 * calls.
 */
export function getWalletActionError(walletAmount: string, walletReason: string): string | null {
  if (!isWalletAmountValid(walletAmount)) {
    return "Enter a positive amount";
  }
  if (!isWalletReasonValid(walletReason)) {
    return "Reason must be at least 3 characters";
  }
  return null;
}
