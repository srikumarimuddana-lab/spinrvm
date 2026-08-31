/**
 * dashboard/rides/_components/ride-flag-form.tsx's `handleSubmit` --
 * validation extracted from the inline `!reason` check per
 * ACTION_ITEMS.md B39 (broader-sweep candidate: admin-dashboard
 * silent-no-op UX gap #2). Unlike this PR's other B39 steps, this one is
 * NOT a pure extraction -- the original check was a silent no-op
 * (`if (!reason) return;`, no toast, no visible error) even though the
 * Flag button's `disabled` prop already blocks the normal click path.
 * This closes the gap for the abnormal paths the `disabled` prop
 * doesn't cover, by surfacing the same toast pattern every other error
 * path on this form already uses.
 */

/** Mirrors `handleSubmit`'s `!!reason` requiredness check. */
export function isRideFlagFormValid(reason: string): boolean {
  return !!reason;
}

/**
 * Returns the same `{ title, description }` shape the form's other
 * `toast(...)` calls use, for the one missing-field case, or null if
 * valid.
 */
export function getRideFlagFormError(reason: string): { title: string; description: string } | null {
  if (!isRideFlagFormValid(reason)) {
    return { title: "Missing reason", description: "Please select a reason for the flag." };
  }
  return null;
}
