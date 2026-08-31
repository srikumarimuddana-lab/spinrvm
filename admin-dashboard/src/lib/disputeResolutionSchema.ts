import { z } from "zod";

/**
 * dashboard/disputes/page.tsx's `handleResolve` -- validation extracted
 * from two inline checks (only run when `resolution === "partial_refund"`)
 * per ACTION_ITEMS.md B39 (broader-sweep candidate: admin-dashboard #2,
 * money tier). Pure extraction, byte-for-byte equivalent of the checks
 * it replaces -- no behavior change.
 *
 * Money-critical: `resolveDispute` posts the resolved refund amount for
 * a rider dispute; a validation gap here has real financial consequence
 * (a refund exceeding the original fare, or a non-positive/garbage
 * amount reaching the backend).
 */

/**
 * Mirrors `handleResolve`'s `isNaN(amount) || amount <= 0` check (inverted
 * to a positive predicate), where `amount = parseFloat(refundAmount)`.
 */
export function isRefundAmountValid(refundAmount: string): boolean {
  const amount = parseFloat(refundAmount);
  return !isNaN(amount) && amount > 0;
}

/**
 * Mirrors `handleResolve`'s `amount > originalFareAmount` check (inverted
 * to a positive predicate). Only meaningful once `isRefundAmountValid`
 * already passed -- mirrors the original's sequential `if` blocks, where
 * this check only runs after the first one passes.
 */
export function isRefundWithinOriginalFare(refundAmount: string, originalFareAmount: number): boolean {
  const amount = parseFloat(refundAmount);
  return amount <= originalFareAmount;
}

export const partialRefundSchema = z
  .object({ refundAmount: z.string(), originalFareAmount: z.number() })
  .refine(({ refundAmount }) => isRefundAmountValid(refundAmount), {
    message: "Refund amount must be greater than zero",
  })
  .refine(({ refundAmount, originalFareAmount }) => isRefundWithinOriginalFare(refundAmount, originalFareAmount), {
    message: "Refund cannot exceed the original fare",
  });

/**
 * Runs the same checks as the page's old inline `handleResolve` partial-
 * refund branch, in the same order (amount validity first, then the
 * fare-cap check), and returns the same error string for the first
 * failing check -- or null if both pass (or if not applicable). Drop-in
 * replacement for the two sequential `if` blocks + `setResolveError(...)`
 * calls; the fare-cap message includes the dollar figure exactly as the
 * original's template string did.
 */
export function getPartialRefundError(refundAmount: string, originalFareAmount: number): string | null {
  if (!isRefundAmountValid(refundAmount)) {
    return "Refund amount must be greater than zero";
  }
  if (!isRefundWithinOriginalFare(refundAmount, originalFareAmount)) {
    return `Refund cannot exceed the original fare of $${originalFareAmount.toFixed(2)}`;
  }
  return null;
}
