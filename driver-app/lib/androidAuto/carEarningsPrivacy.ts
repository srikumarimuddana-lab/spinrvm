/**
 * Earnings-privacy state for the Android Auto surface.
 *
 * The head unit is a SHARED screen. Everything on it is visible to the rider in
 * the back seat, to anyone standing beside an open door, and to whoever the
 * driver hands the car to next — unlike the phone, which the driver controls.
 * So the day's earnings total, which is personal financial information, starts
 * MASKED and is only revealed when the driver deliberately asks for it.
 *
 * Masked is the default on every connect (in-memory only, deliberately not
 * persisted): a reveal is a decision about the situation the driver is in right
 * now, and a preference that survived into the next shift would quietly defeat
 * the whole point.
 *
 * Same channel pattern as carMapCamera.ts — the surface is non-interactive, so
 * the toggle is a MapTemplate map button in register.ts that writes here, and
 * carSurface.tsx subscribes.
 */
import { create } from 'zustand';

interface CarEarningsPrivacyState {
  /** True = the amount is hidden behind the eye icon. Default on every session. */
  hidden: boolean;
  toggle: () => void;
  /** Re-mask. Called on session teardown so the next connect starts private. */
  reset: () => void;
}

export const useCarEarningsPrivacy = create<CarEarningsPrivacyState>((set) => ({
  hidden: true,
  toggle: () => set((s) => ({ hidden: !s.hidden })),
  reset: () => set({ hidden: true }),
}));

/** The mask shown in place of the amount. Same visual width whatever the total. */
export const MASKED_AMOUNT = '••••';

/**
 * What the earnings pill renders: the real amount, or the mask.
 *
 * Pure so the privacy rule itself — never render the number while hidden — is
 * unit-testable without a head unit, and so it cannot be accidentally satisfied
 * by CSS (a number that is merely styled out is still in the view hierarchy and
 * still one screenshot away from being read).
 */
export const displayEarnings = (amount: string | null, hidden: boolean): string | null => {
  if (amount === null) return null;
  return hidden ? MASKED_AMOUNT : amount;
};
