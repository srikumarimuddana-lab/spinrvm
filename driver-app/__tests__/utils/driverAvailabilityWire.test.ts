import type { AvailabilitySnapshotWire } from '@shared/types/driverAvailability';
import {
  classifyAvailabilityError,
  compareDecimal,
  isDecimalString,
  legacySnapshotFromProfile,
  newRequestId,
  normalizeSnapshot,
  parseServerTimeMs,
} from '../../utils/driverAvailabilityWire';

const SESSION = 'controller-session-secret-7f3a';

function v2Wire(overrides: Partial<AvailabilitySnapshotWire> = {}): AvailabilitySnapshotWire {
  return {
    protocol_enabled: true,
    state_version: '8',
    online_epoch: '12',
    is_online: true,
    accepting_requests: true,
    is_available: true,
    availability_state: 'ready',
    reason_code: null,
    eligibility_reason: null,
    controller_session_id: SESSION,
    server_time: '2026-09-24T12:00:00.123456+00:00',
    snapshot_issued_at: '2026-09-24T12:00:00.123456+00:00',
    last_contact_at: '2026-09-24T11:59:50+00:00',
    ready_until: '2026-09-24T13:02:00+00:00',
    active_ride: null,
    pending_offer: null,
    offer_reconciliation_required: false,
    ...overrides,
  };
}

// Structural stand-in for SpinrApiError (shared/api/client.ts:752-787). The
// real class is not importable here: driver jest maps @shared/api/client to
// a mock. The classifier reads only these fields.
function apiError(status: number, body: Record<string, unknown>): Error {
  return Object.assign(new Error('Request failed'), {
    name: 'SpinrApiError', status, code: 0, data: body, response: { data: body, status },
  });
}

describe('isDecimalString / compareDecimal', () => {
  it('accepts int64 max and rejects anything above it', () => {
    expect(isDecimalString('9223372036854775807')).toBe(true);
    expect(isDecimalString('9223372036854775808')).toBe(false);
    expect(isDecimalString('9999999999999999999')).toBe(false);
  });

  it('rejects non-decimal input', () => {
    for (const bad of ['', '-1', '1.0', ' 1', '1e3', '١٢', '12345678901234567890', 7, null, undefined]) {
      expect(isDecimalString(bad)).toBe(false);
    }
  });

  it('orders by value, ignoring leading zeros', () => {
    expect(compareDecimal('9', '10')).toBe(-1);
    expect(compareDecimal('10', '9')).toBe(1);
    expect(compareDecimal('007', '7')).toBe(0);
    expect(compareDecimal('0000000000000000001', '1')).toBe(0);
    expect(compareDecimal('0', '0000')).toBe(0);
    // Adjacent values above 2^53 that Number would treat as equal.
    expect(compareDecimal('9007199254740993', '9007199254740992')).toBe(1);
    expect(compareDecimal('9223372036854775806', '9223372036854775807')).toBe(-1);
  });

  it('returns null when either side is invalid', () => {
    expect(compareDecimal('7', 'x')).toBeNull();
    expect(compareDecimal(7, '7')).toBeNull();
    expect(compareDecimal('9223372036854775808', '1')).toBeNull();
  });
});

describe('parseServerTimeMs', () => {
  const MS = Date.UTC(2026, 8, 24, 12, 0, 0, 123);

  it('cuts 6-digit Postgres fractions to milliseconds', () => {
    expect(parseServerTimeMs('2026-09-24T12:00:00.123456+00:00')).toBe(MS);
    expect(parseServerTimeMs('2026-09-24T12:00:00.123999Z')).toBe(MS);
  });

  it('pads short fractions and accepts a missing fraction', () => {
    expect(parseServerTimeMs('2026-09-24T12:00:00.1+00:00')).toBe(MS - 23);
    expect(parseServerTimeMs('2026-09-24T12:00:00Z')).toBe(MS - 123);
  });

  it('applies every offset spelling', () => {
    const noon = Date.UTC(2026, 8, 24, 12, 0, 0);
    expect(parseServerTimeMs('2026-09-24T06:00:00-06:00')).toBe(noon);
    expect(parseServerTimeMs('2026-09-24T17:30:00+0530')).toBe(noon);
    expect(parseServerTimeMs('2026-09-24T17:00:00+05')).toBe(noon);
    expect(parseServerTimeMs('2026-09-24 12:00:00+00:00')).toBe(noon);
  });

  it('reads a zone-less value as UTC, like the backend', () => {
    expect(parseServerTimeMs('2026-09-24T12:00:00.123456')).toBe(MS);
  });

  it('returns null for anything else', () => {
    for (const bad of ['', 'yesterday', '2026-09-24', '2026-13-01T00:00:00Z', 1_700_000_000_000, null]) {
      expect(parseServerTimeMs(bad)).toBeNull();
    }
  });
});

describe('normalizeSnapshot (v2)', () => {
  it('maps fields one to one and times from snapshot_issued_at', () => {
    const snap = normalizeSnapshot(v2Wire({
      snapshot_issued_at: '2026-09-24T12:00:01+00:00',
      readiness_enforced: true,
      readiness_prompt_at: '2026-09-24T13:00:00+00:00',
      pending_offer: {
        id: 'offer-1', ride_id: 'ride-1', offered_at: null,
        expires_at: '2026-09-24T12:00:15+00:00', ride_status: 'searching',
      },
    }));
    expect(snap).toEqual({
      protocol: 'v2',
      serverClock: true,
      synthetic: false,
      stateVersion: '8',
      onlineEpoch: '12',
      isOnline: true,
      acceptingRequests: true,
      availability: 'ready',
      reasonCode: null,
      eligibilityReason: null,
      serverTimeMs: Date.UTC(2026, 8, 24, 12, 0, 1),
      readyUntilMs: Date.UTC(2026, 8, 24, 13, 2),
      readinessPromptAtMs: Date.UTC(2026, 8, 24, 13, 0),
      activeRide: null,
      pendingOffer: { offerId: 'offer-1', rideId: 'ride-1', expiresAtMs: Date.UTC(2026, 8, 24, 12, 0, 15) },
      offerReconciliationRequired: false,
      readinessPolicyEnabled: true,
    });
  });

  it('never copies controller_session_id', () => {
    const snap = normalizeSnapshot(v2Wire());
    expect(snap).not.toBeNull();
    expect(JSON.stringify(snap)).not.toContain(SESSION);
  });

  it('treats a missing readiness_enforced as not enforced', () => {
    expect(normalizeSnapshot(v2Wire())?.readinessPolicyEnabled).toBe(false);
  });

  it('keeps the server state and reason, and maps an unknown reason to null', () => {
    const paused = normalizeSnapshot(v2Wire({ availability_state: 'paused', reason_code: 'LOCATION_STALE' }));
    expect(paused).toMatchObject({ availability: 'paused', reasonCode: 'LOCATION_STALE' });
    const unknown = normalizeSnapshot({ ...v2Wire(), reason_code: 'SOMETHING_NEW' });
    expect(unknown?.reasonCode).toBeNull();
  });

  it('returns null for a malformed payload', () => {
    const cases: unknown[] = [
      null,
      'ready',
      { ...v2Wire(), is_online: 'true' },
      { ...v2Wire(), server_time: 'now' },
      { ...v2Wire(), state_version: 8 },
      { ...v2Wire(), online_epoch: '-1' },
      { ...v2Wire(), availability_state: 'dozing' },
      { ...v2Wire(), active_ride: { id: 'ride-1' } },
      { ...v2Wire(), active_ride: { id: 'ride-1', status: 'completed', updated_at: null } },
      { ...v2Wire(), pending_offer: { id: 'offer-1' } },
    ];
    for (const raw of cases) expect(normalizeSnapshot(raw)).toBeNull();
  });
});

describe('normalizeSnapshot (legacy, flag off)', () => {
  const legacy = (overrides: Partial<AvailabilitySnapshotWire>) => normalizeSnapshot(v2Wire({
    protocol_enabled: false,
    accepting_requests: false,
    availability_state: 'paused',
    reason_code: 'REQUESTS_PAUSED',
    ...overrides,
  }));

  it('keeps an online driver ready despite paused/REQUESTS_PAUSED', () => {
    expect(legacy({})).toMatchObject({
      protocol: 'legacy', onlineEpoch: null, stateVersion: '8', isOnline: true,
      acceptingRequests: true, availability: 'ready', reasonCode: null,
    });
  });

  it('derives offline, active trip and blocked from is_online and the ride', () => {
    expect(legacy({ is_online: false })).toMatchObject({ availability: 'offline', reasonCode: 'OFFLINE_INTENT' });
    expect(legacy({ active_ride: { id: 'ride-1', status: 'in_progress', updated_at: null } }))
      .toMatchObject({ availability: 'paused', reasonCode: 'ACTIVE_TRIP', activeRide: { id: 'ride-1', status: 'in_progress' } });
    expect(legacy({ eligibility_reason: 'LICENSE_EXPIRED' }))
      .toMatchObject({ availability: 'blocked', reasonCode: 'LICENSE_EXPIRED', eligibilityReason: 'LICENSE_EXPIRED' });
  });

  it('falls back to version 0 and never enforces readiness', () => {
    const snap = normalizeSnapshot({ ...v2Wire({ protocol_enabled: false, readiness_enforced: true }), state_version: 'None' });
    expect(snap).toMatchObject({ stateVersion: '0', readinessPolicyEnabled: false });
  });
});

describe('legacySnapshotFromProfile', () => {
  it('uses /drivers/me is_online and the device receive time', () => {
    expect(legacySnapshotFromProfile({ is_online: true, status: 'active' }, 5_000)).toMatchObject({
      protocol: 'legacy', serverClock: false, serverTimeMs: 5_000, isOnline: true,
      acceptingRequests: true, availability: 'ready',
    });
    expect(legacySnapshotFromProfile({ is_online: false }, 5_000))
      .toMatchObject({ availability: 'offline', reasonCode: 'OFFLINE_INTENT' });
  });

  it('returns null without a boolean is_online', () => {
    expect(legacySnapshotFromProfile({}, 5_000)).toBeNull();
    expect(legacySnapshotFromProfile(null, 5_000)).toBeNull();
  });
});

describe('classifyAvailabilityError', () => {
  it('reads a structured SpinrApiError detail', () => {
    const err = apiError(409, {
      success: false,
      detail: { code: 'ONLINE_EPOCH_STALE', reason_code: 'READINESS_EXPIRED', online_epoch: '13' },
    });
    expect(classifyAvailabilityError(err)).toEqual({
      kind: 'conflict', status: 409, code: 'ONLINE_EPOCH_STALE', reasonCode: 'READINESS_EXPIRED',
      onlineEpoch: '13', legacyDetail: null,
    });
  });

  it('keeps a newest-login controller mismatch as a stale-epoch conflict', () => {
    const err = apiError(409, {
      detail: { code: 'ONLINE_EPOCH_STALE', reason_code: 'CONTROLLER_SESSION_MISMATCH', online_epoch: '4' },
    });
    expect(classifyAvailabilityError(err)).toMatchObject({
      kind: 'conflict', code: 'ONLINE_EPOCH_STALE', reasonCode: 'CONTROLLER_SESSION_MISMATCH', onlineEpoch: '4',
    });
  });

  it('drops an unknown code and a malformed epoch but keeps the kind', () => {
    const info = classifyAvailabilityError(apiError(409, { detail: { code: 'BRAND_NEW', online_epoch: 13 } }));
    expect(info).toMatchObject({ kind: 'conflict', code: null, onlineEpoch: null });
  });

  it('keeps a legacy string detail', () => {
    expect(classifyAvailabilityError(apiError(409, { detail: 'Driver is not online' })))
      .toMatchObject({ kind: 'conflict', code: null, legacyDetail: 'Driver is not online' });
  });

  it('classifies a raw fetch Response by status alone', () => {
    const response = { status: 503, ok: false, json: () => Promise.resolve({}) };
    expect(classifyAvailabilityError(response)).toMatchObject({ kind: 'unavailable', status: 503, code: null });
  });

  it('reads an axios-style response', () => {
    const err = { response: { status: 422, data: { detail: { code: 'INVALID_AVAILABILITY_COMMAND' } } } };
    expect(classifyAvailabilityError(err)).toMatchObject({ kind: 'invalid', status: 422, code: 'INVALID_AVAILABILITY_COMMAND' });
  });

  it('treats no status, status 0 and non-objects as network', () => {
    for (const err of [new TypeError('Network request failed'), apiError(0, {}), 'boom', undefined]) {
      expect(classifyAvailabilityError(err)).toMatchObject({ kind: 'network', status: null });
    }
  });

  it('maps each status to a kind', () => {
    const cases: Array<[number, string]> = [
      [401, 'auth'], [400, 'rejected'], [402, 'rejected'], [403, 'rejected'], [404, 'rejected'],
      [408, 'unavailable'], [429, 'unavailable'], [500, 'unavailable'], [503, 'unavailable'],
    ];
    for (const [status, kind] of cases) {
      expect(classifyAvailabilityError(apiError(status, {})).kind).toBe(kind);
    }
  });

  it('ignores a FastAPI validation array', () => {
    const err = apiError(422, { detail: [{ loc: ['body', 'online_epoch'], msg: 'bad' }] });
    expect(classifyAvailabilityError(err)).toMatchObject({ kind: 'invalid', code: null, legacyDetail: null });
  });
});

describe('newRequestId', () => {
  const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

  it('produces a UUIDv4-format key', () => {
    expect(newRequestId()).toMatch(UUID_V4);
    expect(newRequestId()).not.toBe(newRequestId());
  });

  it('stays well-formed for any injected random value', () => {
    for (const value of [0, 0.5, 0.999999, 1, -3, Number.NaN]) {
      expect(newRequestId(() => value)).toMatch(UUID_V4);
    }
    expect(newRequestId(() => 0)).toBe('00000000-0000-4000-8000-000000000000');
  });
});
