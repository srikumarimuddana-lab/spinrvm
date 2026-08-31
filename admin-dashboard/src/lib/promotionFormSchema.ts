import { z } from "zod";

/**
 * dashboard/promotions/page.tsx's `handleSave` -- validation extracted
 * from the six sequential inline checks (code required; discount value
 * required/positive/within its type's cap; optional max-discount cap
 * required/positive/within its own cap; max uses required/positive;
 * expiry date must be in the future) per ACTION_ITEMS.md B39
 * (broader-sweep candidate: admin-dashboard #3, money tier -- the
 * largest single ad hoc validation block found in the sweep). Pure
 * extraction, byte-for-byte equivalent of the checks it replaces -- no
 * behavior change.
 *
 * Money-critical: `createPromotion`/`updatePromotion` write a promo
 * code's discount terms; a validation gap here has real financial
 * consequence (an unbounded discount, a negative/garbage value, or an
 * already-expired code reaching the backend).
 */

export type PromotionFormInput = {
  code: string;
  free_ride: boolean;
  discount_value: string;
  discount_type: "flat" | "percentage";
  max_discount: string;
  max_uses: string;
  expiry_date: string;
};

/** Mirrors `handleSave`'s `!form.code.trim()` check (inverted). */
export function isPromoCodeValid(code: string): boolean {
  return !!code.trim();
}

/** Mirrors `handleSave`'s `!form.discount_value` check (inverted). */
export function isDiscountValueProvided(discountValue: string): boolean {
  return !!discountValue;
}

/**
 * Mirrors `handleSave`'s `isNaN(discountVal) || discountVal <= 0` check
 * (inverted), where `discountVal = parseFloat(form.discount_value)`.
 */
export function isDiscountAmountValid(discountValue: string): boolean {
  const n = parseFloat(discountValue);
  return !isNaN(n) && n > 0;
}

/**
 * Mirrors `handleSave`'s `discount_type === "percentage" && discountVal > 100`
 * check (inverted). Only meaningful once `isDiscountAmountValid` already
 * passed, same as the original's sequential `if` blocks.
 */
export function isPercentageWithinLimit(discountType: "flat" | "percentage", discountValue: string): boolean {
  if (discountType !== "percentage") return true;
  return parseFloat(discountValue) <= 100;
}

/**
 * Mirrors `handleSave`'s `discount_type === "flat" && discountVal > 500`
 * check (inverted).
 */
export function isFlatDiscountWithinLimit(discountType: "flat" | "percentage", discountValue: string): boolean {
  if (discountType !== "flat") return true;
  return parseFloat(discountValue) <= 500;
}

/**
 * Mirrors `handleSave`'s `isNaN(maxD) || maxD <= 0` check (inverted),
 * where `maxD = parseFloat(form.max_discount)`. Only evaluated when
 * `max_discount` is non-empty, same as the original's `if (form.max_discount)`
 * guard.
 */
export function isMaxDiscountValid(maxDiscount: string): boolean {
  const n = parseFloat(maxDiscount);
  return !isNaN(n) && n > 0;
}

/** Mirrors `handleSave`'s `maxD > 500` check (inverted). */
export function isMaxDiscountWithinLimit(maxDiscount: string): boolean {
  return parseFloat(maxDiscount) <= 500;
}

/**
 * Mirrors `handleSave`'s `isNaN(maxUses) || maxUses < 1` check (inverted),
 * where `maxUses = parseInt(form.max_uses)`.
 */
export function isMaxUsesValid(maxUses: string): boolean {
  const n = parseInt(maxUses);
  return !isNaN(n) && n >= 1;
}

/**
 * Mirrors `handleSave`'s `expiry <= new Date()` check (inverted). Only
 * evaluated when `expiry_date` is non-empty, same as the original's
 * `if (form.expiry_date)` guard.
 */
export function isExpiryDateValid(expiryDate: string): boolean {
  if (!expiryDate) return true;
  return new Date(expiryDate) > new Date();
}

export const promotionFormSchema = z
  .object({
    code: z.string(),
    free_ride: z.boolean(),
    discount_value: z.string(),
    discount_type: z.enum(["flat", "percentage"]),
    max_discount: z.string(),
    max_uses: z.string(),
    expiry_date: z.string(),
  })
  .superRefine((form, ctx) => {
    if (!isPromoCodeValid(form.code)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Please fill in the code.", path: ["code"] });
      return;
    }
    if (!form.free_ride) {
      if (!isDiscountValueProvided(form.discount_value)) {
        ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Please fill in code and discount value.", path: ["discount_value"] });
        return;
      }
      if (!isDiscountAmountValid(form.discount_value)) {
        ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Discount must be greater than zero.", path: ["discount_value"] });
        return;
      }
      if (!isPercentageWithinLimit(form.discount_type, form.discount_value)) {
        ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Percentage discount cannot exceed 100%.", path: ["discount_value"] });
        return;
      }
      if (!isFlatDiscountWithinLimit(form.discount_type, form.discount_value)) {
        ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Flat discount cannot exceed $500.", path: ["discount_value"] });
        return;
      }
      if (form.max_discount) {
        if (!isMaxDiscountValid(form.max_discount)) {
          ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Max discount cap must be greater than zero.", path: ["max_discount"] });
          return;
        }
        if (!isMaxDiscountWithinLimit(form.max_discount)) {
          ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Max discount cap cannot exceed $500.", path: ["max_discount"] });
          return;
        }
      }
    }
    if (!isMaxUsesValid(form.max_uses)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Max uses must be at least 1.", path: ["max_uses"] });
      return;
    }
    if (!isExpiryDateValid(form.expiry_date)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Expiry date must be in the future.", path: ["expiry_date"] });
    }
  });

/**
 * Runs the same checks as the page's old inline `handleSave` validation
 * block, in the same order, and returns the same `{ title, description }`
 * pair the original passed to `toast(...)` for the first failing check --
 * or null if all pass. Drop-in replacement for the six sequential `if`
 * blocks + `toast({...})` calls.
 */
export function getPromotionFormError(form: PromotionFormInput): { title: string; description: string } | null {
  if (!isPromoCodeValid(form.code)) {
    return { title: "Missing required fields", description: "Please fill in the code." };
  }
  if (!form.free_ride) {
    if (!isDiscountValueProvided(form.discount_value)) {
      return { title: "Missing required fields", description: "Please fill in code and discount value." };
    }
    if (!isDiscountAmountValid(form.discount_value)) {
      return { title: "Invalid discount value", description: "Discount must be greater than zero." };
    }
    if (!isPercentageWithinLimit(form.discount_type, form.discount_value)) {
      return { title: "Invalid percentage", description: "Percentage discount cannot exceed 100%." };
    }
    if (!isFlatDiscountWithinLimit(form.discount_type, form.discount_value)) {
      return { title: "Discount too large", description: "Flat discount cannot exceed $500." };
    }
    if (form.max_discount) {
      if (!isMaxDiscountValid(form.max_discount)) {
        return { title: "Invalid max discount cap", description: "Max discount cap must be greater than zero." };
      }
      if (!isMaxDiscountWithinLimit(form.max_discount)) {
        return { title: "Max discount cap too large", description: "Max discount cap cannot exceed $500." };
      }
    }
  }
  if (!isMaxUsesValid(form.max_uses)) {
    return { title: "Invalid max uses", description: "Max uses must be at least 1." };
  }
  if (!isExpiryDateValid(form.expiry_date)) {
    return { title: "Invalid expiry date", description: "Expiry date must be in the future." };
  }
  return null;
}
