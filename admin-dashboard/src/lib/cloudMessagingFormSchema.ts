import { z } from "zod";

/**
 * dashboard/cloud-messaging/page.tsx's two ad hoc validation blocks --
 * the broadcast-compose form's `handleSend` (title/description required;
 * recipients required for a "particular" audience; a schedule time
 * required when scheduling; at least one delivery channel selected) and
 * the marketing-suppression form's `handleAddSuppression` (a target
 * email/phone required) -- per ACTION_ITEMS.md B39 (broader-sweep
 * candidate: admin-dashboard #6, PIPEDA-adjacent). Pure extraction,
 * byte-for-byte equivalent of the checks they replace -- no behavior
 * change.
 *
 * PIPEDA-adjacent: the suppression list is the do-not-market opt-out
 * mechanism; a validation gap there (an empty/garbage target silently
 * accepted) would mean an opt-out request doesn't actually take effect.
 */

export type BroadcastAudience = string;

export type BroadcastFormInput = {
  title: string;
  description: string;
  audience: BroadcastAudience;
  particularIds: unknown[];
  isScheduled: boolean;
  scheduledAt: string;
  sendPush: boolean;
  sendEmail: boolean;
  sendSms: boolean;
};

/** Mirrors `handleSend`'s `!form.title.trim() || !form.description.trim()` check (inverted). */
export function isBroadcastTitleDescriptionValid(title: string, description: string): boolean {
  return !!title.trim() && !!description.trim();
}

/** Mirrors `handleSend`'s `isParticular` computation. */
export function isParticularAudience(audience: BroadcastAudience): boolean {
  return audience === "particular_customer" || audience === "particular_driver";
}

/** Mirrors `handleSend`'s `isParticular && form.particular_ids.length === 0` check (inverted). */
export function isRecipientsSelected(audience: BroadcastAudience, particularIds: unknown[]): boolean {
  if (!isParticularAudience(audience)) return true;
  return particularIds.length > 0;
}

/** Mirrors `handleSend`'s `form.is_scheduled && !form.scheduled_at` check (inverted). */
export function isScheduleTimeValid(isScheduled: boolean, scheduledAt: string): boolean {
  if (!isScheduled) return true;
  return !!scheduledAt;
}

/** Mirrors `handleSend`'s `channels.length === 0` check (inverted). */
export function isDeliveryChannelSelected(sendPush: boolean, sendEmail: boolean, sendSms: boolean): boolean {
  return sendPush || sendEmail || sendSms;
}

/**
 * Runs the same checks as the broadcast-compose form's old inline
 * `handleSend` validation, in the same order, and returns the same
 * `{ title, description }` pair the original passed to `toast(...)` for
 * the first failing check, or null if all pass.
 */
export function getBroadcastFormError(form: BroadcastFormInput): { title: string; description: string } | null {
  if (!isBroadcastTitleDescriptionValid(form.title, form.description)) {
    return { title: "Missing fields", description: "Please fill in title and description." };
  }
  if (!isRecipientsSelected(form.audience, form.particularIds)) {
    return { title: "No recipients selected", description: "Please select at least one user/driver." };
  }
  if (!isScheduleTimeValid(form.isScheduled, form.scheduledAt)) {
    return { title: "Missing schedule time", description: "Please select a date and time." };
  }
  if (!isDeliveryChannelSelected(form.sendPush, form.sendEmail, form.sendSms)) {
    return { title: "No delivery channel", description: "Please select at least one delivery channel." };
  }
  return null;
}

/** Mirrors `handleAddSuppression`'s `!newSupp.target.trim()` check (inverted). */
export function isSuppressionTargetValid(target: string): boolean {
  return !!target.trim();
}

export const suppressionFormSchema = z.object({ target: z.string() }).superRefine(({ target }, ctx) => {
  if (!isSuppressionTargetValid(target)) {
    ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Enter an email or phone to suppress.", path: ["target"] });
  }
});

/**
 * Runs the same check as the marketing-suppression form's old inline
 * `handleAddSuppression` validation, and returns the same
 * `{ title, description }` pair, or null if valid.
 */
export function getSuppressionFormError(target: string): { title: string; description: string } | null {
  if (!isSuppressionTargetValid(target)) {
    return { title: "Missing value", description: "Enter an email or phone to suppress." };
  }
  return null;
}
