import { z } from "zod";

/**
 * dashboard/safety/page.tsx's two ad hoc validation blocks -- the manual
 * "Log a safety incident" form's `handleSubmit` (category + description
 * required) and the incident-detail drawer's duplicate-merge flow's
 * `handleMerge` (canonical target ID required, cannot merge into itself)
 * -- per ACTION_ITEMS.md B39 (broader-sweep candidate: admin-dashboard #5,
 * safety tier). Pure extraction, byte-for-byte equivalent of the checks
 * they replace -- no behavior change.
 *
 * Safety-critical: both feed the safety-incident record (a manually
 * logged incident, or a merge that collapses a duplicate report into its
 * canonical incident) -- a validation gap here has real safety/audit-trail
 * consequence (an incomplete incident record, or a self-merge that could
 * corrupt the merge graph).
 */

/** Mirrors `handleSubmit`'s `!category.trim() || !description.trim()` check (inverted). */
export function isLogIncidentFormValid(category: string, description: string): boolean {
  return !!category.trim() && !!description.trim();
}

/**
 * Runs the same check as the "Log a safety incident" dialog's old inline
 * `handleSubmit` validation, and returns the same error string, or null
 * if valid.
 */
export function getLogIncidentFormError(category: string, description: string): string | null {
  if (!isLogIncidentFormValid(category, description)) {
    return "Category and description are required";
  }
  return null;
}

/** Mirrors `handleMerge`'s `!targetId` check (inverted), where `targetId = mergeTargetId.trim()`. */
export function isMergeTargetProvided(mergeTargetId: string): boolean {
  return !!mergeTargetId.trim();
}

/** Mirrors `handleMerge`'s `targetId === incident.id` check (inverted). */
export function isMergeTargetDifferentFromIncident(mergeTargetId: string, incidentId: string): boolean {
  return mergeTargetId.trim() !== incidentId;
}

export const mergeIncidentSchema = z
  .object({ mergeTargetId: z.string(), incidentId: z.string() })
  .superRefine(({ mergeTargetId, incidentId }, ctx) => {
    if (!isMergeTargetProvided(mergeTargetId)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Enter the canonical incident ID to merge into", path: ["mergeTargetId"] });
      return;
    }
    if (!isMergeTargetDifferentFromIncident(mergeTargetId, incidentId)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Cannot merge an incident into itself", path: ["mergeTargetId"] });
    }
  });

/**
 * Runs the same checks as the incident-detail drawer's old inline
 * `handleMerge` validation, in the same order, and returns the same
 * error string for the first failing check, or null if both pass.
 */
export function getMergeIncidentError(mergeTargetId: string, incidentId: string): string | null {
  if (!isMergeTargetProvided(mergeTargetId)) {
    return "Enter the canonical incident ID to merge into";
  }
  if (!isMergeTargetDifferentFromIncident(mergeTargetId, incidentId)) {
    return "Cannot merge an incident into itself";
  }
  return null;
}
