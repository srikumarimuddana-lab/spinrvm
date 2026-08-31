import { z } from "zod";

/**
 * dashboard/subscriptions/page.tsx's `PlanModal.handleSubmit` -- validation
 * extracted from two inline checks per ACTION_ITEMS.md B39 (the "Plans" tab
 * on the top-level subscriptions list, distinct from the per-company
 * `subscription/page.tsx` and the wallet-adjustment form already migrated
 * in step 8). Each helper below is a byte-for-byte equivalent of the check
 * it replaces, kept separate (rather than one aggregate boolean) because
 * the call site shows a different error message per failing check.
 *
 * This form creates/edits Spinr Pass subscription plans (price, duration,
 * rides/day) -- not itself a money-movement call, but the `price` field
 * feeds Stripe Checkout/Billing on the driver side, so a validation gap
 * here could let a negative price reach the plan record.
 */

export const planNameSchema = z.string().trim().min(1);

/**
 * Mirrors `handleSubmit`'s `!form.name.trim()` guard -- the "Plan name is
 * required." error's condition.
 */
export function isPlanNameValid(name: string): boolean {
  return planNameSchema.safeParse(name).success;
}

export const planPriceSchema = z.number().min(0);

/**
 * Mirrors `handleSubmit`'s `form.price < 0` guard -- the separate
 * "Price must be ≥ 0." error's condition. Kept apart from
 * `isPlanNameValid` because the two failures show different messages at
 * the call site (matches the original's two sequential `if` blocks, not
 * one combined check).
 */
export function isPlanPriceValid(price: number): boolean {
  return price >= 0;
}
