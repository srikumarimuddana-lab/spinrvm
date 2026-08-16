/**
 * Wire contract for the SOS endpoint, shared by rider-app and driver-app.
 *
 * `POST /rides/{ride_id}/emergency` has always returned per-contact delivery
 * status, but nothing on the rider path read it: both `SOSButton.onTrigger`
 * and `rideStore.triggerEmergency` were typed `Promise<void>` and discarded
 * the body. The rider was therefore told "your emergency contacts have been
 * notified" on any HTTP 200 -- including when every Twilio send failed, and
 * including when the rider had no emergency contacts saved at all.
 *
 * A safety flow must not claim a notification it cannot confirm, so the
 * response shape is pinned here and consumed by the UI. Mirrors the return of
 * `backend/routes/rides/safety.py::trigger_emergency`.
 */

/** Per-contact SMS outcome. `notified` is false when Twilio rejected or errored. */
export interface SOSContactStatus {
  id: string;
  name: string;
  notified: boolean;
}

export interface SOSTriggerResult {
  success: boolean;
  incident_id: string;
  /** Count of emergency contacts whose SMS actually succeeded. May be 0. */
  contacts_notified: number;
  /**
   * One entry per stored emergency contact. Empty when the rider has none
   * saved -- which is NOT an error, but must not be reported as "contacts
   * notified" either.
   */
  contacts: SOSContactStatus[];
  /**
   * Present only when the contact-notification block threw as a whole (the
   * backend still returns 200 because the incident itself was persisted and
   * the safety team was alerted). Copy shown to the user must reflect this.
   */
  notification_warning?: string;
  /**
   * True when this request was deduplicated against an earlier press with the
   * same idempotency key (migration 315) -- i.e. the original alert already
   * fired and this retry deliberately triggered no new side effects. The
   * per-contact outcome of that original send is NOT re-derivable here, so
   * `contacts` comes back empty and the UI must say "unknown" rather than
   * "no contacts saved".
   */
  duplicate?: boolean;
}

/**
 * What the alert actually achieved, derived from the response so the UI never
 * has to infer it. The incident always reached our safety team by the time any
 * of these are returned -- these describe the *contact* leg only.
 */
export type SOSContactOutcome =
  /** At least one emergency contact was reached by SMS. */
  | 'contacts_notified'
  /** Contacts are configured but none could be reached. */
  | 'contacts_failed'
  /** No emergency contacts are saved on the account. */
  | 'no_contacts'
  /**
   * The alert reached our safety team but the contact outcome cannot be
   * determined -- a deduplicated replay, or a caller that doesn't surface the
   * response body. Must not be reported as either success or "none saved".
   */
  | 'unknown';

export function deriveContactOutcome(result: SOSTriggerResult | void | null): SOSContactOutcome {
  // A caller that returns nothing (a void-typed onTrigger, or a path that
  // doesn't surface contacts) tells us nothing about the contact leg. Say so,
  // rather than inventing either outcome.
  if (!result || typeof result !== 'object') return 'unknown';

  // A deduped replay deliberately performed no new sends and cannot report on
  // the original one. contacts[] is empty here for that reason, NOT because
  // the user has none saved -- checked before the emptiness test below.
  if (result.duplicate) return 'unknown';

  // The backend sets this when the whole contact-notification block threw. It
  // still returns 200 (the incident is persisted, the safety team alerted),
  // but no contact was reached.
  if (result.notification_warning) return 'contacts_failed';

  if (!Array.isArray(result.contacts) || result.contacts.length === 0) {
    return 'no_contacts';
  }
  return (result.contacts_notified ?? 0) > 0 ? 'contacts_notified' : 'contacts_failed';
}
