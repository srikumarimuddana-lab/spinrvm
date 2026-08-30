import { z } from "zod";

/**
 * dashboard/service-areas/page.tsx's `GeneralTabForm`... no -- this one is
 * the top-level `ServiceAreasPage`'s `handleFieldUpdate`. Validation
 * extracted from the tax-configuration justification prompt per
 * ACTION_ITEMS.md B39. Per the page's own A29 comment: "GST/PST/HST
 * config carries real regulatory + financial weight (every rider's
 * charge, CRA/SK remittance), so the backend now requires a written
 * justification for any of these fields -- mirroring the existing
 * surge-above-cap justification prompt" (see `surgeJustificationSchema.ts`,
 * B39 step 10, for that sibling gate).
 *
 * This is a UX gate only -- the backend independently requires
 * `tax_justification` on a GST/PST/HST field change and 400s without one
 * (per the A29 comment: "rather than letting the save silently 400"); a
 * blank justification here still can't reach `updateServiceArea` (the
 * network call), exactly as before.
 */

export const taxJustificationSchema = z.string().trim().min(1);

/**
 * Mirrors `handleFieldUpdate`'s `!justification` guard, where
 * `justification` is already `window.prompt(...)?.trim()`'s result (a
 * `string | undefined`, `undefined` on Cancel). Accepts a non-empty
 * string after trimming; rejects `undefined`/`null`/empty/whitespace-only.
 */
export function isTaxJustificationValid(justification: string | null | undefined): boolean {
  return taxJustificationSchema.safeParse(justification ?? "").success;
}
