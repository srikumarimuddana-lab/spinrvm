/**
 * Pure parsing and classification for the driver availability v2 wire
 * contract (shared/types/driverAvailability.ts). Headless-safe: no React
 * Native, Expo, zustand or API-client imports, so background tasks can use it.
 */
import type {
  AvailabilityErrorCode,
  AvailabilityErrorInfo,
  AvailabilityErrorKind,
  AvailabilityReasonCode,
  AvailabilitySnapshot,
  AvailabilityStateWire,
  DecimalString,
  EligibilityReasonCode,
} from '@shared/types/driverAvailability';

type WireRecord = Record<string, unknown>;

// Each table must list exactly its union's members; tsc rejects a missing or
// unknown key, so the runtime checks cannot drift from the shared types.
const ELIGIBILITY_REASONS: Record<EligibilityReasonCode, true> = {
  ACCOUNT_SUSPENDED: true, ACCOUNT_REVIEW: true, ACCOUNT_INELIGIBLE: true, DOCUMENT_EXPIRED: true,
  LICENSE_EXPIRED: true, INSURANCE_EXPIRED: true, DRIVER_UNVERIFIED: true,
  SUBSCRIPTION_REQUIRED: true, QUOTA_EXHAUSTED: true,
};
const AVAILABILITY_REASONS: Record<AvailabilityReasonCode, true> = {
  ...ELIGIBILITY_REASONS,
  ACTIVE_TRIP: true, OFFER_PENDING: true, RECOVERY_REQUIRED: true, SESSION_RECONCILE_REQUIRED: true,
  SESSION_SUPERSEDED: true, READY_TIMEOUT: true, PRESENCE_UNAVAILABLE: true, LOCATION_STALE: true,
  POLICY_BLOCKED: true, MISSED_OFFERS: true, REQUESTS_STOPPED: true, REQUESTS_PAUSED: true,
  OFFLINE_INTENT: true,
};
const AVAILABILITY_STATES: Record<AvailabilityStateWire, true> = {
  offline: true, ready: true, reconnecting: true, paused: true, blocked: true,
};
const ERROR_CODES: Record<AvailabilityErrorCode, true> = {
  DRIVER_OFFLINE: true, ONLINE_EPOCH_STALE: true, CONTACT_GAP: true, SESSION_SUPERSEDED: true,
  SESSION_RECONCILE_REQUIRED: true, PRESENCE_UNAVAILABLE: true, ELIGIBILITY_UNAVAILABLE: true,
  ELIGIBILITY_BLOCKED: true, AVAILABILITY_UPGRADE_REQUIRED: true, AVAILABILITY_V2_DISABLED: true,
  AVAILABILITY_UNAVAILABLE: true, UNAUTHORIZED_SESSION: true, CONTROLLER_SESSION_MISMATCH: true,
  IDEMPOTENCY_KEY_CONFLICT: true, OBLIGATION_ACTIVE: true, INVALID_AVAILABILITY_COMMAND: true,
  DRIVER_NOT_FOUND: true, SESSION_AUTHORITY_UNAVAILABLE: true, READINESS_EXPIRED: true,
  REQUESTS_PAUSED: true, OFFER_EXPIRED: true, RIDE_STATE_CONFLICT: true, CLAIM_MISMATCH: true,
  OFFER_ALREADY_RESOLVED: true, OFFER_NOT_FOUND: true, INVALID_RECEIPT: true,
  OFFER_PROTOCOL_MISMATCH: true,
};
// The snapshot's active_trip CTE selects only these (migration 457).
const ACTIVE_RIDE_STATUSES = new Set(['driver_assigned', 'driver_accepted', 'driver_arrived', 'in_progress']);

function isRecord(value: unknown): value is WireRecord {
  return typeof value === 'object' && value !== null;
}

function member<K extends string>(table: Record<K, true>, value: unknown): value is K {
  return typeof value === 'string' && Object.prototype.hasOwnProperty.call(table, value);
}

const DECIMAL_RE = /^\d{1,19}$/;
const INT64_MAX = '9223372036854775807';

function stripLeadingZeros(value: string): string {
  const digits = value.replace(/^0+/, '');
  return digits === '' ? '0' : digits;
}

/** 1–19 ASCII digits and at most int64 max: what the backend accepts as an epoch. */
export function isDecimalString(value: unknown): value is DecimalString {
  if (typeof value !== 'string' || !DECIMAL_RE.test(value)) return false;
  const digits = stripLeadingZeros(value);
  return digits.length < INT64_MAX.length || digits <= INT64_MAX;
}

/**
 * Orders decimal strings without Number (int64 exceeds 2^53) or BigInt (no
 * Hermes dependency). Null when either side is not a decimal string.
 */
export function compareDecimal(a: unknown, b: unknown): -1 | 0 | 1 | null {
  if (!isDecimalString(a) || !isDecimalString(b)) return null;
  const x = stripLeadingZeros(a);
  const y = stripLeadingZeros(b);
  if (x.length !== y.length) return x.length < y.length ? -1 : 1;
  if (x === y) return 0;
  return x < y ? -1 : 1;
}

const TIMESTAMP_RE = /^(\d{4}-\d{2}-\d{2})[Tt ](\d{2}:\d{2}:\d{2})(?:[.,](\d+))?([Zz]|[+-]\d{2}(?::?\d{2})?)?$/;

/**
 * Epoch ms of a server timestamp, or null. The value is rewritten into the
 * one format every ECMAScript engine must parse (3 fraction digits, `Z` or
 * `±HH:mm`), so Postgres's 1–6 digit fractions never depend on Hermes's
 * leniency. A value without a zone is UTC, as the backend's `_as_utc` reads it.
 */
export function parseServerTimeMs(value: unknown): number | null {
  if (typeof value !== 'string') return null;
  const match = TIMESTAMP_RE.exec(value);
  if (!match) return null;
  const [, date, time, fraction = '', zone = 'Z'] = match;
  const offset = zone.toUpperCase() === 'Z'
    ? 'Z'
    : `${zone.slice(0, 3)}:${zone.slice(3).replace(':', '') || '00'}`;
  const ms = Date.parse(`${date}T${time}.${fraction.slice(0, 3).padEnd(3, '0')}${offset}`);
  return Number.isFinite(ms) ? ms : null;
}

// `undefined` means malformed: the whole snapshot is then rejected.
function parseActiveRide(value: unknown): AvailabilitySnapshot['activeRide'] | undefined {
  if (value === null || value === undefined) return null;
  if (!isRecord(value) || typeof value.id !== 'string' || value.id === '') return undefined;
  if (typeof value.status !== 'string' || !ACTIVE_RIDE_STATUSES.has(value.status)) return undefined;
  return { id: value.id, status: value.status };
}

function parsePendingOffer(value: unknown): AvailabilitySnapshot['pendingOffer'] | undefined {
  if (value === null || value === undefined) return null;
  if (!isRecord(value) || typeof value.id !== 'string' || value.id === '') return undefined;
  if (typeof value.ride_id !== 'string' || value.ride_id === '') return undefined;
  return { offerId: value.id, rideId: value.ride_id, expiresAtMs: parseServerTimeMs(value.expires_at) };
}

/**
 * Normalizes a snapshot GET body (or a command response carrying one). Null
 * means the payload breaks the contract; callers treat it as a failed
 * reconcile. `controller_session_id` is never copied.
 */
export function normalizeSnapshot(raw: unknown): AvailabilitySnapshot | null {
  if (!isRecord(raw) || typeof raw.is_online !== 'boolean') return null;
  const serverTimeMs = parseServerTimeMs(raw.server_time);
  const activeRide = parseActiveRide(raw.active_ride);
  const pendingOffer = parsePendingOffer(raw.pending_offer);
  if (serverTimeMs === null || activeRide === undefined || pendingOffer === undefined) return null;
  const isOnline = raw.is_online;
  const eligibilityReason = member(ELIGIBILITY_REASONS, raw.eligibility_reason) ? raw.eligibility_reason : null;
  const common = {
    serverClock: true,
    synthetic: false,
    isOnline,
    eligibilityReason,
    serverTimeMs: parseServerTimeMs(raw.snapshot_issued_at) ?? serverTimeMs,
    readyUntilMs: parseServerTimeMs(raw.ready_until),
    readinessPromptAtMs: parseServerTimeMs(raw.readiness_prompt_at),
    activeRide,
    pendingOffer,
    offerReconciliationRequired: raw.offer_reconciliation_required === true,
  };
  if (raw.protocol_enabled === true) {
    if (!isDecimalString(raw.state_version) || !isDecimalString(raw.online_epoch)) return null;
    if (!member(AVAILABILITY_STATES, raw.availability_state)) return null;
    return {
      ...common,
      protocol: 'v2',
      stateVersion: raw.state_version,
      onlineEpoch: raw.online_epoch,
      acceptingRequests: raw.accepting_requests === true,
      availability: raw.availability_state,
      reasonCode: member(AVAILABILITY_REASONS, raw.reason_code) ? raw.reason_code : null,
      readinessPolicyEnabled: raw.readiness_enforced === true,
    };
  }
  // Flag off: legacy writes never set accepting_requests (migration 457
  // default false), so the server's availability_state/reason_code would
  // call every online driver paused/REQUESTS_PAUSED. Derive from is_online.
  const blocked = typeof raw.eligibility_reason === 'string' && raw.eligibility_reason !== '';
  return {
    ...common,
    protocol: 'legacy',
    stateVersion: isDecimalString(raw.state_version) ? raw.state_version : '0',
    onlineEpoch: null,
    acceptingRequests: isOnline,
    availability: blocked ? 'blocked' : isOnline ? (activeRide ? 'paused' : 'ready') : 'offline',
    reasonCode: blocked ? eligibilityReason : activeRide ? 'ACTIVE_TRIP' : isOnline ? null : 'OFFLINE_INTENT',
    readinessPolicyEnabled: false,
  };
}

/**
 * For servers without the snapshot GET (404/405): /drivers/me's `is_online`,
 * timed by the device clock at receipt.
 */
export function legacySnapshotFromProfile(profile: unknown, receivedAtMs: number): AvailabilitySnapshot | null {
  if (!isRecord(profile) || typeof profile.is_online !== 'boolean' || !Number.isFinite(receivedAtMs)) return null;
  const isOnline = profile.is_online;
  return {
    protocol: 'legacy',
    serverClock: false,
    synthetic: false,
    stateVersion: '0',
    onlineEpoch: null,
    isOnline,
    acceptingRequests: isOnline,
    availability: isOnline ? 'ready' : 'offline',
    reasonCode: isOnline ? null : 'OFFLINE_INTENT',
    eligibilityReason: null,
    serverTimeMs: receivedAtMs,
    readyUntilMs: null,
    readinessPromptAtMs: null,
    activeRide: null,
    pendingOffer: null,
    offerReconciliationRequired: false,
    readinessPolicyEnabled: false,
  };
}

function httpStatus(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 100 && value <= 599 ? value : null;
}

function kindForStatus(status: number | null): AvailabilityErrorKind {
  if (status === null) return 'network';
  if (status === 401) return 'auth';
  if (status === 409) return 'conflict';
  if (status === 422) return 'invalid';
  if (status === 408 || status === 429 || status >= 500) return 'unavailable';
  // Other 4xx are definitive. 'unsupported' (snapshot GET 404/405) is the
  // adapter's call, since only it knows which endpoint answered.
  return status >= 400 ? 'rejected' : 'unavailable';
}

/**
 * Classifies anything an availability call can throw: SpinrApiError (status
 * 0 when unknown), RateLimitError, an axios-style `response`, a raw fetch
 * Response, or a network/timeout Error with no status at all.
 */
export function classifyAvailabilityError(err: unknown): AvailabilityErrorInfo {
  const error: WireRecord = isRecord(err) ? err : {};
  const response: WireRecord = isRecord(error.response) ? error.response : {};
  const status = httpStatus(error.status) ?? httpStatus(response.status);
  const detail = (isRecord(error.data) ? error.data.detail : undefined)
    ?? (isRecord(response.data) ? response.data.detail : undefined);
  const structured = isRecord(detail) && !Array.isArray(detail) ? detail : null;
  return {
    kind: kindForStatus(status),
    status,
    code: structured && member(ERROR_CODES, structured.code) ? structured.code : null,
    reasonCode: structured && typeof structured.reason_code === 'string' ? structured.reason_code : null,
    onlineEpoch: structured && isDecimalString(structured.online_epoch) ? structured.online_epoch : null,
    legacyDetail: typeof detail === 'string' ? detail : null,
  };
}

/**
 * UUIDv4-format idempotency key, generated like `generateRequestId` in
 * shared/api/client.ts. It is not a secret, so Math.random suffices and
 * headless and test code need no expo-crypto.
 */
export function newRequestId(random: () => number = Math.random): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = Math.min(15, Math.max(0, (random() * 16) | 0));
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}
