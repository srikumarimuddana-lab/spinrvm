/**
 * dashboard/rides/_components/ride-complaint-form.tsx's `handleSubmit` --
 * validation extracted from the inline `!category || !description`
 * check per ACTION_ITEMS.md B39 (broader-sweep candidate: admin-dashboard
 * silent-no-op UX gap #1). Unlike this PR's other B39 steps, this one is
 * NOT a pure extraction -- the original check was a silent no-op
 * (`if (!category || !description) return;`, no toast, no visible error)
 * even though the Submit button's `disabled` prop already blocks the
 * normal click path. This closes the gap for the abnormal paths the
 * `disabled` prop doesn't cover (a race between state updates and the
 * click handler, a form submit via Enter in a text field, etc.) by
 * surfacing the same toast pattern every other error path on this form
 * already uses.
 */

/** Mirrors `handleSubmit`'s `category && description` requiredness check. */
export function isRideComplaintFormValid(category: string, description: string): boolean {
  return !!category && !!description;
}

/**
 * Returns the same `{ title, description }` shape the form's other
 * `toast(...)` calls use, for the one missing-field case, or null if
 * valid.
 */
export function getRideComplaintFormError(category: string, description: string): { title: string; description: string } | null {
  if (!isRideComplaintFormValid(category, description)) {
    return { title: "Missing fields", description: "Please select a category and describe the issue." };
  }
  return null;
}
