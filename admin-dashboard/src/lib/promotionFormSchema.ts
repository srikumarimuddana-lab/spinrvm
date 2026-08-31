import { z } from "zod";

/**
 * dashboard/promotions/page.tsx's `handleSave` -- validation extracted
 * from the largest single inline-check block found in the ACTION_ITEMS.md
 * B39 broader sweep (admin-dashboard #3, money tier): code presence,
 * discount value/type/cap checks (skipped entirely for a `free_ride`
 * promo), max-uses, and a future-expiry-date check. Pure extraction,
 * byte-for-byte equivalent of the checks it replaces -- no behavior
 * change.
 *
 * Money-critical: `createPromotion`/`updatePromotion` writes a promo
 * code that directly discounts a rider's fare at redemption; a
 * validation gap here has real financial consequence (an unbounded
 * discount, a >100% percentage, or a promo that can never expire).
 */

export interface PromotionFormField {
  code: string;
  freeRide: boolean;
  discountType: string;
  discountValue: string;
  maxDiscount: string;
  maxUses: string;
  expiryDate: string;
}

// Individual predicates are exported so the toast-branching call site
// (and any future non-toast caller) can reuse them independently,
// matching the pattern used by profileSetupSchema.ts and
// disputeResolutionSchema.ts.

/** Mirrors `!form.code.trim()` (inverted to a positive predicate). */
export function isPromoCodeValid(code: string): boolean {
  return code.trim().length > 0;
}

/** Mirrors `!form.discount_value` (inverted) -- presence only, not shape. */
export function isDiscountValuePresent(discountValue: string): boolean {
  return !!discountValue;
}

/**
 * Mirrors `isNaN(discountVal) || discountVal <= 0` (inverted), where
 * `discountVal = parseFloat(discountValue)`.
 */
export function isDiscountValueValid(discountValue: string): boolean {
  const discountVal = parseFloat(discountValue);
  return !isNaN(discountVal) && discountVal > 0;
}

/**
 * Mirrors `discount_type === "percentage" && discountVal > 100` (inverted)
 * -- always true (no cap) for a non-percentage discount type, matching
 * the original's `&&`-gated check.
 */
export function isPercentageDiscountValid(discountType: string, discountValue: string): boolean {
  if (discountType !== "percentage") return true;
  return parseFloat(discountValue) <= 100;
}

/**
 * Mirrors `discount_type === "flat" && discountVal > 500` (inverted) --
 * always true (no cap) for a non-flat discount type.
 */
export function isFlatDiscountValid(discountType: string, discountValue: string): boolean {
  if (discountType !== "flat") return true;
  return parseFloat(discountValue) <= 500;
}

/**
 * Mirrors `isNaN(maxD) || maxD <= 0` (inverted), only checked when
 * `form.max_discount` is set -- always true (optional field) when empty.
 */
export function isMaxDiscountValid(maxDiscount: string): boolean {
  if (!maxDiscount) return true;
  const maxD = parseFloat(maxDiscount);
  return !isNaN(maxD) && maxD > 0;
}

/** Mirrors `maxD > 500` (inverted), only checked when `max_discount` is set. */
export function isMaxDiscountWithinLimit(maxDiscount: string): boolean {
  if (!maxDiscount) return true;
  return parseFloat(maxDiscount) <= 500;
}

/**
 * Mirrors `isNaN(maxUses) || maxUses < 1` (inverted), where
 * `maxUses = parseInt(form.max_uses)`.
 */
export function isMaxUsesValid(maxUses: string): boolean {
  const parsed = parseInt(maxUses);
  return !isNaN(parsed) && parsed >= 1;
}

/**
 * Mirrors `expiry <= new Date()` (inverted), only checked when
 * `form.expiry_date` is set -- always true (optional field) when empty.
 */
export function isExpiryDateValid(expiryDate: string): boolean {
  if (!expiryDate) return true;
  return new Date(expiryDate) > new Date();
}

export const promotionFormSchema = z
  .object({
    code: z.string(),
    freeRide: z.boolean(),
    discountType: z.string(),
    discountValue: z.string(),
    maxDiscount: z.string(),
    maxUses: z.string(),
    expiryDate: z.string(),
  })
  .refine((f) => isPromoCodeValid(f.code), { message: "Please fill in the code." })
  .refine((f) => f.freeRide || isDiscountValuePresent(f.discountValue), {
    message: "Please fill in code and discount value.",
  })
  .refine((f) => f.freeRide || isDiscountValueValid(f.discountValue), {
    message: "Discount must be greater than zero.",
  })
  .refine((f) => f.freeRide || isPercentageDiscountValid(f.discountType, f.discountValue), {
    message: "Percentage discount cannot exceed 100%.",
  })
  .refine((f) => f.freeRide || isFlatDiscountValid(f.discountType, f.discountValue), {
    message: "Flat discount cannot exceed $500.",
  })
  .refine((f) => f.freeRide || isMaxDiscountValid(f.maxDiscount), {
    message: "Max discount cap must be greater than zero.",
  })
  .refine((f) => f.freeRide || isMaxDiscountWithinLimit(f.maxDiscount), {
    message: "Max discount cap cannot exceed $500.",
  })
  .refine((f) => isMaxUsesValid(f.maxUses), { message: "Max uses must be at least 1." })
  .refine((f) => isExpiryDateValid(f.expiryDate), { message: "Expiry date must be in the future." });

export type PromotionFormInput = z.infer<typeof promotionFormSchema>;

/**
 * Runs the same checks as the page's old inline `handleSave` block, in
 * the same order (code, then -- only when not a free-ride promo --
 * discount presence/value/percentage-cap/flat-cap/max-discount-cap, then
 * max-uses, then expiry), and returns the same `{title, description}`
 * toast pair for the first failing check -- or null if every check
 * passes (or is skipped). Drop-in replacement for the sequential `if`
 * blocks + `toast({...})` calls.
 */
export function getPromotionFormError(
  form: PromotionFormField,
): { title: string; description: string } | null {
  if (!isPromoCodeValid(form.code)) {
    return { title: "Missing required fields", description: "Please fill in the code." };
  }
  if (!form.freeRide) {
    if (!isDiscountValuePresent(form.discountValue)) {
      return { title: "Missing required fields", description: "Please fill in code and discount value." };
    }
    if (!isDiscountValueValid(form.discountValue)) {
      return { title: "Invalid discount value", description: "Discount must be greater than zero." };
    }
    if (!isPercentageDiscountValid(form.discountType, form.discountValue)) {
      return { title: "Invalid percentage", description: "Percentage discount cannot exceed 100%." };
    }
    if (!isFlatDiscountValid(form.discountType, form.discountValue)) {
      return { title: "Discount too large", description: "Flat discount cannot exceed $500." };
    }
    if (form.maxDiscount) {
      if (!isMaxDiscountValid(form.maxDiscount)) {
        return { title: "Invalid max discount cap", description: "Max discount cap must be greater than zero." };
      }
      if (!isMaxDiscountWithinLimit(form.maxDiscount)) {
        return { title: "Max discount cap too large", description: "Max discount cap cannot exceed $500." };
      }
    }
  }
  if (!isMaxUsesValid(form.maxUses)) {
    return { title: "Invalid max uses", description: "Max uses must be at least 1." };
  }
  if (form.expiryDate && !isExpiryDateValid(form.expiryDate)) {
    return { title: "Invalid expiry date", description: "Expiry date must be in the future." };
  }
  return null;
}
