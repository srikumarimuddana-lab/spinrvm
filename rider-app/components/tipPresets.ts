/**
 * Fare-scaled tip presets.
 *
 * Extracted from ride-completed.tsx so the two rules that make it correct are
 * directly testable: the ladder must always be strictly increasing (three
 * identical buttons on a small fare is a bug), and a selection must not survive
 * a ladder it no longer belongs to (the rider would be charged an amount with no
 * highlighted button — see reconcileSelectedTip).
 */

/** Percentages the ladder is built from, in ascending order. */
export const TIP_PERCENTAGES = [0.15, 0.18, 0.2] as const;

/**
 * Whole-dollar tip presets for a fare.
 *
 * Whole dollars because that is what actually gets charged — offering "18%" and
 * billing $4.53 invites disputes. Floored at $1 and forced strictly increasing,
 * because on small fares every percentage rounds to the same dollar and the UI
 * would render duplicate buttons (with duplicate React keys).
 *
 * A zero/unknown fare yields [1, 2, 3]. That is a placeholder, not a design —
 * callers must not let it be selected (see `isTipLadderReady`).
 */
export function computeTipOptions(fare: number): number[] {
  const base = Number.isFinite(fare) && fare > 0 ? fare : 0;
  const values = TIP_PERCENTAGES.map((pct) => Math.max(1, Math.round(base * pct)));
  for (let i = 1; i < values.length; i += 1) {
    if (values[i] <= values[i - 1]) values[i] = values[i - 1] + 1;
  }
  return values;
}

/** Whether the ladder reflects a real fare and may be offered to the rider. */
export function isTipLadderReady(fare: number): boolean {
  return Number.isFinite(fare) && fare > 0;
}

/**
 * The selection that should survive a ladder change.
 *
 * Returns null when the previously-selected amount is no longer on the ladder.
 * This screen is reachable before the fare loads (a push-notification tap cold-
 * starts into it), so a rider can tap a placeholder preset and have the amounts
 * change underneath them. Keeping the stale number would charge a tip that
 * matches no visible button — the selection is dropped instead, so what is
 * charged always equals what is highlighted.
 */
export function reconcileSelectedTip(selected: number | null, options: number[]): number | null {
  if (selected === null) return null;
  return options.includes(selected) ? selected : null;
}
