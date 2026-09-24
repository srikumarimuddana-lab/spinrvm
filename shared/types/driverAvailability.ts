/**
 * Driver availability v2 contract (PR #5727), shared by driver-app and the
 * rider-app type-check (rider-app/tsconfig.json compiles ../shared/types/**).
 *
 * TYPES ONLY: this file must never gain a runtime export. Import it with
 * `import type` — driver-app's jest maps `@shared/*` to `__mocks__/@shared`,
 * so a value import would resolve to a mock that does not exist.
 *
 * Wire shapes mirror backend/services/driver_availability_service.py
 * (snapshot), routes/drivers/status.py and routes/drivers/location.py (error
 * details) and routes/websocket.py, as amended by
 * .claude/plans/2026-09-24-driver-availability-contract-decisions.md (C1–C6).
 * Fields a server may not send yet are optional; where absence has a meaning,
 * the field's own comment states it.
 */

/** Decimal string, /^\d{1,19}$/ and <= int64 max. Never convert to number. */
export type DecimalString = string;
/** RFC 3339; Postgres values may carry 1–6 fractional digits. */
export type IsoTimestamp = string;

export type AvailabilityStateWire = 'offline' | 'ready' | 'reconnecting' | 'paused' | 'blocked';

/** `eligibility_reason`; SUBSCRIPTION_REQUIRED and QUOTA_EXHAUSTED arrive with T4-7. */
export type EligibilityReasonCode =
  | 'ACCOUNT_SUSPENDED' | 'ACCOUNT_REVIEW' | 'ACCOUNT_INELIGIBLE' | 'DOCUMENT_EXPIRED'
  | 'LICENSE_EXPIRED' | 'INSURANCE_EXPIRED' | 'DRIVER_UNVERIFIED'
  | 'SUBSCRIPTION_REQUIRED' | 'QUOTA_EXHAUSTED';

/** Snapshot `reason_code`. QUOTA_EXHAUSTED is also mapped client-side from WS `auto_offline`. */
export type AvailabilityReasonCode =
  | EligibilityReasonCode
  | 'ACTIVE_TRIP' | 'OFFER_PENDING' | 'RECOVERY_REQUIRED' | 'SESSION_RECONCILE_REQUIRED'
  | 'SESSION_SUPERSEDED' | 'READY_TIMEOUT' | 'PRESENCE_UNAVAILABLE' | 'LOCATION_STALE'
  | 'POLICY_BLOCKED' | 'MISSED_OFFERS' | 'REQUESTS_STOPPED' | 'REQUESTS_PAUSED' | 'OFFLINE_INTENT';

export interface AvailabilityActiveRideWire {
  id: string;
  status: string;
  updated_at: IsoTimestamp | null;
}

/** C1 envelope keys, carried by every v2 offer channel. Absent from legacy offers. */
export interface OfferEnvelopeFieldsWire {
  offer_protocol?: 'v2';
  /** `ride_offers.id`; null or absent for admin-direct offers. */
  offer_id?: string | null;
  claim_id?: string | null;
  online_epoch?: DecimalString | null;
  /** Database time when the offer was made. */
  server_time?: IsoTimestamp | null;
  expires_at?: IsoTimestamp | null;
}

export interface AvailabilityPendingOfferWire extends OfferEnvelopeFieldsWire {
  id: string;
  ride_id: string;
  offered_at: IsoTimestamp | null;
  expires_at: IsoTimestamp | null;
  ride_status: string | null;
}

/** `GET /api/v1/drivers/me/availability` (driver_availability_service.py:252-270). */
export interface AvailabilitySnapshotWire {
  protocol_enabled: boolean;
  state_version: DecimalString;
  online_epoch: DecimalString;
  is_online: boolean;
  accepting_requests: boolean;
  is_available: boolean;
  availability_state: AvailabilityStateWire;
  reason_code: AvailabilityReasonCode | null;
  eligibility_reason: EligibilityReasonCode | null;
  /** Opaque (C11): the client never reads, logs or persists it. */
  controller_session_id: string | null;
  server_time: IsoTimestamp;
  snapshot_issued_at: IsoTimestamp;
  last_contact_at: IsoTimestamp | null;
  ready_until: IsoTimestamp | null;
  active_ride: AvailabilityActiveRideWire | null;
  pending_offer: AvailabilityPendingOfferWire | null;
  offer_reconciliation_required: boolean;
  /** C4 (F2-3). Absent on older servers, which never enforce readiness. */
  readiness_enforced?: boolean;
  /** C4: `ready_until` minus the prompt lead; null when `ready_until` is null. */
  readiness_prompt_at?: IsoTimestamp | null;
}

/** `PUT /drivers/{id}/status` `availability_action` values (status.py:320). */
export type StatusCommandAction = 'go_online' | 'go_offline' | 'stop_requests';
/** Every availability command; `confirm_ready` uses POST /drivers/me/availability (C4). */
export type AvailabilityCommandAction = StatusCommandAction | 'confirm_ready';

/** Extra PUT-status body fields for a v2 command. */
export interface AvailabilityCommandFields {
  online_epoch: DecimalString;
  /** Idempotency key, at most 128 characters. */
  request_id: string;
  availability_action: StatusCommandAction;
}
export interface StatusCommandSuccessWire extends AvailabilitySnapshotWire {
  success: true;
  code: 'OK';
  transition: Record<string, unknown>;
}
/** Flag-off success (status.py, last line of update_driver_status). */
export interface LegacyStatusSuccessWire {
  success: true;
  is_online: boolean;
}
/** C4: `POST /api/v1/drivers/me/availability`. Never replaced by a go_online fallback. */
export interface ConfirmReadyRequestWire {
  action: 'confirm_ready';
  online_epoch: DecimalString;
  request_id: string;
}
export interface ConfirmReadySuccessWire extends AvailabilitySnapshotWire {
  code: 'OK';
}

/** `detail.code` values. Read them from `err.data.detail.code` or `err.response.data.detail.code`. */
export type AvailabilityErrorCode =
  | 'DRIVER_OFFLINE' | 'ONLINE_EPOCH_STALE' | 'CONTACT_GAP' | 'SESSION_SUPERSEDED'
  | 'SESSION_RECONCILE_REQUIRED' | 'PRESENCE_UNAVAILABLE' | 'ELIGIBILITY_UNAVAILABLE'
  | 'ELIGIBILITY_BLOCKED' | 'AVAILABILITY_UPGRADE_REQUIRED' | 'AVAILABILITY_V2_DISABLED'
  | 'AVAILABILITY_UNAVAILABLE' | 'UNAUTHORIZED_SESSION' | 'CONTROLLER_SESSION_MISMATCH'
  | 'IDEMPOTENCY_KEY_CONFLICT' | 'OBLIGATION_ACTIVE' | 'INVALID_AVAILABILITY_COMMAND'
  | 'DRIVER_NOT_FOUND' | 'SESSION_AUTHORITY_UNAVAILABLE'
  // Readiness confirm (C4).
  | 'READINESS_EXPIRED' | 'REQUESTS_PAUSED'
  // Offer decisions and receipts (C2, C3).
  | 'OFFER_EXPIRED' | 'RIDE_STATE_CONFLICT' | 'CLAIM_MISMATCH' | 'OFFER_ALREADY_RESOLVED'
  | 'OFFER_NOT_FOUND' | 'INVALID_RECEIPT' | 'OFFER_PROTOCOL_MISMATCH';

/** `detail.reason_code` values, which qualify a `detail.code`. */
export type AvailabilityErrorReasonCode =
  | EligibilityReasonCode // under ELIGIBILITY_BLOCKED
  | 'ONLINE_EPOCH_STALE' | 'CONTACT_GAP' | 'READINESS_EXPIRED' // under ONLINE_EPOCH_STALE
  | 'UNAUTHORIZED_SESSION' | 'CONTROLLER_SESSION_MISMATCH' // under SESSION_SUPERSEDED (C6, F3)
  | 'SESSION_MISSING' | 'ONLINE_EPOCH_REQUIRED' // location.py:132-137
  | 'RIDE_TAKEN' | 'RIDE_CANCELLED' | 'RIDE_NOT_SEARCHING'; // under RIDE_STATE_CONFLICT (C2)

/** Structured `detail` of a 4xx/5xx; 5xx details keep only the C6 (F1) allow-listed keys. */
export interface AvailabilityErrorDetailWire {
  code: AvailabilityErrorCode | string;
  message?: string;
  reason_code?: AvailabilityErrorReasonCode | string | null;
  online_epoch?: DecimalString | null;
  state_version?: DecimalString;
  has_trip?: boolean;
  has_pending_offer?: boolean;
  retry_after_ms?: number;
  offer_id?: string | null;
  expires_at?: IsoTimestamp | null;
  server_time?: IsoTimestamp | null;
  ride_status?: string | null;
  /** C2: best-effort fresh snapshot, same JSON as the snapshot GET. */
  snapshot?: AvailabilitySnapshotWire;
}

/** Extra keys on WS `auth_success` for a v2 driver socket (websocket.py:893-904). */
export interface WsAuthSuccessAvailabilityWire {
  availability_protocol?: 'v2';
  presence_status?: 'bound' | 'reconcile_required';
  online_epoch?: DecimalString | null;
}
export interface WsAvailabilityReconcileRequiredWire {
  type: 'availability_reconcile_required';
  code: AvailabilityErrorCode | string;
  online_epoch: DecimalString | null;
}
/** C4: a hint to reconcile; the snapshot stays the authority. */
export interface WsAvailabilityChangedWire {
  type: 'availability_changed';
  online_epoch: DecimalString;
  state_version: DecimalString;
  reason_code: AvailabilityReasonCode | string | null;
  server_time: IsoTimestamp;
}
/** C4: show the readiness prompt, then reconcile. */
export interface WsAvailabilityReadinessPromptWire {
  type: 'availability_readiness_prompt';
  online_epoch: DecimalString;
  ready_until: IsoTimestamp;
  server_time: IsoTimestamp;
}

/** Offer on WS `new_ride_assignment`, FCM data (all values strings) or the stored-offer GET. */
export interface OfferEnvelopeWire extends OfferEnvelopeFieldsWire {
  ride_id: string;
  /** Same value as `expires_at` on v2 servers. */
  offer_expires_at?: IsoTimestamp | null;
  countdown_seconds?: number | string | null;
}

export type OfferReceiptEvent = 'received' | 'presented';
export type OfferReceiptChannel = 'ws' | 'push' | 'stored' | 'android_auto';
/** React Native `AppState` names (C3); `presented` requires 'active'. */
export type OfferReceiptAppState = 'active' | 'background' | 'inactive' | 'unknown';
/** C3: `POST /api/v1/drivers/offers/{offer_id}/receipts`. */
export interface OfferReceiptRequestWire {
  claim_id: string;
  event: OfferReceiptEvent;
  channel: OfferReceiptChannel;
  app_state: OfferReceiptAppState;
  remaining_ms: number;
}

// ── Normalized domain model (driver-app/utils/driverAvailabilityWire.ts) ──

export type AuthDomain = 'restoring' | 'authenticated' | 'recoverable' | 'reauth_required';
export type AvailabilityDomain =
  | 'unknown' | 'offline' | 'connecting' | 'ready' | 'reconnecting' | 'paused' | 'blocked';
export type WorkDomain = 'idle' | 'offer_pending' | 'on_trip';

export interface AvailabilitySnapshot {
  protocol: 'v2' | 'legacy';
  /** False only for the /drivers/me fallback, whose time is the device's. */
  serverClock: boolean;
  /** True for a local placeholder built from a server signal; never authorizes uploads. */
  synthetic: boolean;
  stateVersion: DecimalString;
  onlineEpoch: DecimalString | null;
  isOnline: boolean;
  acceptingRequests: boolean;
  availability: Exclude<AvailabilityDomain, 'unknown' | 'connecting'>;
  reasonCode: AvailabilityReasonCode | null;
  eligibilityReason: EligibilityReasonCode | null;
  serverTimeMs: number;
  readyUntilMs: number | null;
  readinessPromptAtMs: number | null;
  activeRide: { id: string; status: string } | null;
  pendingOffer: { offerId: string; rideId: string; expiresAtMs: number | null } | null;
  offerReconciliationRequired: boolean;
  /** v2 and `readiness_enforced === true`. */
  readinessPolicyEnabled: boolean;
}

export type AvailabilityErrorKind =
  | 'conflict' | 'unavailable' | 'auth' | 'network' | 'unsupported' | 'invalid' | 'rejected';
export interface AvailabilityErrorInfo {
  kind: AvailabilityErrorKind;
  status: number | null;
  /** Null when the server sent no code or one this client does not know. */
  code: AvailabilityErrorCode | null;
  reasonCode: string | null;
  onlineEpoch: DecimalString | null;
  /** A plain-string `detail` (flag-off servers). */
  legacyDetail: string | null;
}
