import { z } from 'zod';

/**
 * ride-completed.tsx's custom-tip amount computation, extracted from four
 * duplicated inline occurrences of `customTip ? parseFloat(customTip) || 0
 * : 0` per ACTION_ITEMS.md B39.
 *
 * BUG FIX (not a pure byte-for-byte extraction): the original expression's
 * `parseFloat(customTip) || 0` fallback does NOT catch a negative value --
 * `-5 || 0` evaluates to `-5` (any non-zero number is truthy in JS), so a
 * rider typing "-5" into the custom-tip field would have sent -$5 straight
 * into the Stripe charge amount (`confirmPayment`) and the driver rating
 * call (`rateRide(..., tipAmount > 0 ? tipAmount : undefined)`). This
 * extraction closes that gap by explicitly rejecting negative and
 * non-finite values, clamping them to 0 -- the same "no tip" outcome an
 * empty field already produced for every other input the original code
 * handled identically (empty string, whitespace, non-numeric text all
 * already produced 0 via the same `|| 0` fallback).
 */
export const customTipRawSchema = z.string();

/**
 * Computes the effective custom-tip dollar amount from the raw text-field
 * value. Mirrors the original `customTip ? parseFloat(customTip) || 0 : 0`
 * for every input except negative numbers (see BUG FIX above).
 */
export function getCustomTipAmount(customTip: string): number {
  if (!customTip) return 0;
  const parsed = parseFloat(customTip);
  if (!Number.isFinite(parsed) || parsed < 0) return 0;
  return parsed;
}
