/**
 * Should Work Mode apply its corporate-billing default right now?
 *
 * Work Mode means "bill my company by default", so a work-mode rider who has
 * not chosen a payment source should get corporate — that is the feature.
 * What must NOT happen is work mode re-asserting that default *after* the
 * rider has deliberately chosen otherwise.
 *
 * The bug this guards against is a timing one, which is why it needs a
 * function rather than being obvious inline. The screens sync work mode in a
 * `useEffect` that re-runs when `activeCompanyId` / the corporate-account list
 * changes — and a `fetchWorkProfiles()` resolving a moment after mount does
 * exactly that. Sequence:
 *
 *   1. Work-mode rider opens the payment screen  → default applies, corporate.
 *   2. Rider deliberately taps their personal card → useCorporate = false.
 *   3. The profile fetch lands                    → effect re-runs.
 *   4. Without this guard, step 3 silently flips the toggle back to corporate.
 *
 * The ride is then billed to the COMPANY for a personal trip, and neither the
 * rider nor the company is told. That is a wrong charge — the reason this is
 * guarded rather than left to "it's only a toggle".
 *
 * `riderChosePayment` is tracked per screen-session in a ref, so it resets
 * when the rider leaves and comes back — a fresh booking correctly gets the
 * work-mode default again.
 */
export function shouldApplyWorkModeDefault(params: {
  workModeEnabled: boolean;
  /** Rider actually has at least one corporate account to bill. */
  hasCorporateAccounts: boolean;
  /** Rider has explicitly picked a payment source during this screen session. */
  riderChosePayment: boolean;
}): boolean {
  const { workModeEnabled, hasCorporateAccounts, riderChosePayment } = params;
  if (!workModeEnabled) return false;
  if (!hasCorporateAccounts) return false;
  // An explicit choice always wins, in either direction.
  return !riderChosePayment;
}
