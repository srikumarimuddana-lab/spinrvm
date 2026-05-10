import en from '../i18n/en.json';

describe('rider-app i18n error coverage', () => {
  // Walk every leaf string under errors.*; collect dotted paths.
  const collect = (obj: Record<string, unknown>, prefix: string): string[] => {
    const out: string[] = [];
    for (const [k, v] of Object.entries(obj)) {
      const path = prefix ? `${prefix}.${k}` : k;
      if (typeof v === 'string') out.push(path);
      else if (v && typeof v === 'object') out.push(...collect(v as Record<string, unknown>, path));
    }
    return out;
  };

  const errorTree = (en as { errors?: Record<string, unknown> }).errors ?? {};
  const allKeys = collect(errorTree, 'errors');

  it('has at least 28 canonical error keys', () => {
    expect(allKeys.length).toBeGreaterThanOrEqual(28);
  });

  it.each([
    'errors.auth.account_suspended',
    'errors.auth.otp_invalid',
    'errors.auth.otp_expired',
    'errors.auth.otp_locked',
    'errors.auth.invalid_credentials',
    'errors.auth.token_expired',
    'errors.driver.documents_expired',
    'errors.driver.documents_pending',
    'errors.driver.documents_rejected',
    'errors.driver.subscription_required',
    'errors.driver.not_available',
    'errors.driver.offline',
    'errors.ride.not_found',
    'errors.ride.already_cancelled',
    'errors.ride.invalid_status',
    'errors.ride.no_drivers_available',
    'errors.ride.price_mismatch',
    'errors.ride.taken',
    'errors.payment.failed',
    'errors.payment.method_invalid',
    'errors.payment.insufficient_funds',
    'errors.payment.refund_failed',
    'errors.system.internal',
    'errors.system.service_unavailable',
    'errors.system.rate_limited',
    'errors.system.database',
    'errors.unknown',
  ])('has key %s with non-empty English copy', (key) => {
    const parts = key.split('.');
    let node: unknown = en;
    for (const p of parts) {
      expect(node).toBeDefined();
      node = (node as Record<string, unknown>)[p];
    }
    expect(typeof node).toBe('string');
    expect((node as string).length).toBeGreaterThan(0);
  });
});
