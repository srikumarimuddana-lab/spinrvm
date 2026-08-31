import { z } from "zod";

/**
 * dashboard/rides/_components/create-ride-modal.tsx's `handleSubmit` --
 * validation extracted from the four sequential inline checks (rider
 * selected; pickup selected; dropoff selected; total fare override is a
 * non-negative number) per ACTION_ITEMS.md B39 (broader-sweep candidate:
 * admin-dashboard #4, money tier -- admin fare override). Pure
 * extraction, byte-for-byte equivalent of the checks it replaces -- no
 * behavior change.
 *
 * Money-critical: `total_fare` (when set) is an admin's manual override
 * of the estimated fare, posted directly via `adminCreateRide`; a
 * validation gap here (a negative or garbage total) has real financial
 * consequence.
 */

/** Mirrors `handleSubmit`'s `!selectedRider` check (inverted). */
export function isRiderSelected(rider: unknown): boolean {
  return !!rider;
}

/** Mirrors `handleSubmit`'s `!selectedPickup` check (inverted). */
export function isPickupSelected(pickup: unknown): boolean {
  return !!pickup;
}

/** Mirrors `handleSubmit`'s `!selectedDropoff` check (inverted). */
export function isDropoffSelected(dropoff: unknown): boolean {
  return !!dropoff;
}

/**
 * Mirrors `handleSubmit`'s `Number.isNaN(totalNum) || totalNum < 0` check
 * (inverted), where `totalNum = parseFloat(finalFare || "0")`.
 */
export function isFareAmountValid(finalFare: string): boolean {
  const n = parseFloat(finalFare || "0");
  return !Number.isNaN(n) && n >= 0;
}

export const createRideFormSchema = z
  .object({
    rider: z.unknown(),
    pickup: z.unknown(),
    dropoff: z.unknown(),
    finalFare: z.string(),
  })
  .superRefine((form, ctx) => {
    if (!isRiderSelected(form.rider)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Please select a rider.", path: ["rider"] });
      return;
    }
    if (!isPickupSelected(form.pickup)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Please select a valid pickup location from the suggestions.", path: ["pickup"] });
      return;
    }
    if (!isDropoffSelected(form.dropoff)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Please select a valid dropoff location from the suggestions.", path: ["dropoff"] });
      return;
    }
    if (!isFareAmountValid(form.finalFare)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Total fare must be a non-negative number.", path: ["finalFare"] });
    }
  });

/**
 * Runs the same checks as the modal's old inline `handleSubmit`
 * validation block, in the same order, and returns the same error
 * string `setError(...)` was called with for the first failing check --
 * or null if all pass. Drop-in replacement for the four sequential
 * early-return `if` blocks.
 */
export function getCreateRideFormError(form: {
  rider: unknown;
  pickup: unknown;
  dropoff: unknown;
  finalFare: string;
}): string | null {
  if (!isRiderSelected(form.rider)) {
    return "Please select a rider.";
  }
  if (!isPickupSelected(form.pickup)) {
    return "Please select a valid pickup location from the suggestions.";
  }
  if (!isDropoffSelected(form.dropoff)) {
    return "Please select a valid dropoff location from the suggestions.";
  }
  if (!isFareAmountValid(form.finalFare)) {
    return "Total fare must be a non-negative number.";
  }
  return null;
}
