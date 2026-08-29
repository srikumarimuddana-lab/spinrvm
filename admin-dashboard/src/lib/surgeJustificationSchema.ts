import { z } from "zod";

/**
 * dashboard/service-areas/page.tsx's `GeneralTabForm.handleSave` --
 * validation extracted from the surge-override justification gate per
 * ACTION_ITEMS.md B39. Mirrors CLAUDE.md's Critical Conventions surge
 * rule: "Admin manual override accepts 1.0-10.0 but any value > 2.5
 * requires documented justification (regulatory + reputational risk)."
 *
 * This is a UX gate only -- the server-side clamp to SURGE_CAP (2.5x) at
 * every fare-calc call site is unaffected by this change; a justification
 * left blank here still can't reach `onSave` (the network call), exactly
 * as before.
 */

// Mirrors `backend/utils/surge_engine.py`'s SURGE_CAP -- the admin-override
// justification threshold, not the enforced clamp (that stays server-side).
export const SURGE_JUSTIFICATION_THRESHOLD = 2.5;

/**
 * Mirrors `GeneralTabForm`'s `needsJustification = form.surge_enabled &&
 * surgeValue > 2.5` -- true only when surge is enabled AND the multiplier
 * exceeds the threshold. A disabled or at-or-under-cap multiplier never
 * needs justification.
 */
export function needsSurgeJustification(surgeEnabled: boolean, multiplier: number): boolean {
  return surgeEnabled && multiplier > SURGE_JUSTIFICATION_THRESHOLD;
}

export const surgeJustificationSchema = z.string().trim().min(1);

/**
 * Mirrors `handleSave`'s `!form.surge_justification.trim()` guard -- the
 * alert()'s condition. Required, non-empty after trimming.
 */
export function isSurgeJustificationValid(justification: string): boolean {
  return surgeJustificationSchema.safeParse(justification).success;
}
