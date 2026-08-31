/**
 * app/report-safety.tsx's `handleSubmit` -- validation extracted from
 * the two sequential inline checks (category required; description
 * required) per ACTION_ITEMS.md B39 (broader-sweep candidate:
 * driver-app #3, safety). Each helper below is a byte-for-byte
 * equivalent of the check it replaces -- see the PR description for the
 * before/after per call site.
 *
 * Safety-critical: this is the driver-facing safety-incident report
 * form -- a validation gap here (a report submitted with no category or
 * description) degrades the trust-and-safety team's ability to triage.
 */

/** Mirrors `handleSubmit`'s `!category` check (inverted). */
export function isSafetyCategoryValid(category: string | null): boolean {
  return !!category;
}

/** Mirrors `handleSubmit`'s `!issue.trim()` check (inverted). */
export function isSafetyIssueValid(issue: string): boolean {
  return !!issue.trim();
}

/**
 * Runs the same checks as `handleSubmit`'s old inline validation block,
 * in the same order, and returns the same `{ title, message }` pair the
 * original passed to `showToast('warning', ...)` for the first failing
 * check, or null if both pass.
 */
export function getReportSafetyFormError(category: string | null, issue: string): { title: string; message: string } | null {
  if (!isSafetyCategoryValid(category)) {
    return { title: 'Category Required', message: 'Please select a category for your safety report.' };
  }
  if (!isSafetyIssueValid(issue)) {
    return { title: 'Description Required', message: 'Please describe the safety issue before submitting.' };
  }
  return null;
}
