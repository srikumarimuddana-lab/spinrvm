import { z } from "zod";

/**
 * dashboard/service-areas/page.tsx's `SpinrPassAreaTab.handleSubmit` --
 * validation extracted from two inline checks per ACTION_ITEMS.md B39.
 * This is a second, separate Spinr Pass plan create/edit form -- distinct
 * from `subscriptions/page.tsx`'s `PlanModal` (B39 step 9) despite both
 * ultimately calling the same `createSubscriptionPlan`/
 * `updateSubscriptionPlan` API. The two forms are NOT byte-for-byte
 * equivalent to each other: this one keeps `price` as a raw string field
 * (`useState({ price: "" , ... })`, parsed with `parseFloat` only at
 * submit time) and checks `!form.price` (empty-string truthy check,
 * requires non-empty -- "0" passes, "" fails), whereas step 9's
 * `subscriptionPlanSchema.ts` checks a numeric `form.price < 0` on an
 * already-`number`-typed field. Kept as separate schema files rather than
 * reusing step 9's predicates, since the underlying types genuinely
 * differ and merging them would be a validation-rule change, not a pure
 * extraction. Note: neither the original code nor this extraction guards
 * against a non-numeric price string (e.g. "abc" -> `parseFloat` ->
 * `NaN` sent to the backend) -- that gap predates this change and is
 * preserved as-is, not silently fixed here.
 */

export const spinrPassPlanNameSchema = z.string().min(1);

/** Mirrors `handleSubmit`'s `!form.name` guard. */
export function isSpinrPassPlanNameValid(name: string): boolean {
  return spinrPassPlanNameSchema.safeParse(name).success;
}

export const spinrPassPlanPriceSchema = z.string().min(1);

/**
 * Mirrors `handleSubmit`'s `!form.price` guard -- a non-empty-string
 * check on the raw price input, not a numeric validity check (see file
 * header).
 */
export function isSpinrPassPlanPriceValid(price: string): boolean {
  return spinrPassPlanPriceSchema.safeParse(price).success;
}
