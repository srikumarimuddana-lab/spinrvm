import { describe, it, expect } from 'vitest';
import {
  createIssueExplainer,
  explainSinDobIssue,
  explainVehicleHistoryIssue,
  explainSavedAddressIssue,
} from '../bulk-import-error-help';

describe('createIssueExplainer', () => {
  it('matches an exact message', () => {
    const explain = createIssueExplainer({ foo: { cause: 'c', fix: 'f' } });
    expect(explain('foo')).toEqual({ cause: 'c', fix: 'f' });
  });

  it('matches a prefix rule for a message with a dynamic suffix', () => {
    const explain = createIssueExplainer({}, [['bar:', { cause: 'c', fix: 'f' }]]);
    expect(explain('bar: some dynamic detail')).toEqual({ cause: 'c', fix: 'f' });
  });

  it('returns null for an unmapped message', () => {
    const explain = createIssueExplainer({ foo: { cause: 'c', fix: 'f' } });
    expect(explain('unmapped')).toBeNull();
  });

  it('prefers an exact match over a prefix match', () => {
    const explain = createIssueExplainer({ 'bar: exact': { cause: 'exact', fix: 'exact' } }, [
      ['bar:', { cause: 'prefix', fix: 'prefix' }],
    ]);
    expect(explain('bar: exact')).toEqual({ cause: 'exact', fix: 'exact' });
  });
});

describe('explainSinDobIssue', () => {
  it('matches the shared driver-crosswalk messages', () => {
    expect(explainSinDobIssue('no Spinr driver with this phone number')).not.toBeNull();
  });

  it('matches the dynamic invalid-SIN prefix', () => {
    const explanation = explainSinDobIssue('invalid SIN, skipped: SIN must be 9 digits; got 8');
    expect(explanation).not.toBeNull();
    expect(explanation?.fix).toMatch(/mistyped digit/i);
  });

  it('matches the duplicate-phone message', () => {
    expect(explainSinDobIssue('duplicate phone match within this batch; first row wins')).not.toBeNull();
  });
});

describe('explainVehicleHistoryIssue', () => {
  it('matches the shared driver-crosswalk messages', () => {
    expect(explainVehicleHistoryIssue('matched driver is not a known legacy-imported driver; skipped')).not.toBeNull();
  });

  it('matches its own created_at message', () => {
    expect(explainVehicleHistoryIssue('missing or unparseable created_at')).not.toBeNull();
  });

  it('does not carry the SIN/DOB-only duplicate-phone message', () => {
    expect(explainVehicleHistoryIssue('duplicate phone match within this batch; first row wins')).toBeNull();
  });
});

describe('explainSavedAddressIssue', () => {
  it('matches its own messages', () => {
    expect(explainSavedAddressIssue('no matching Spinr rider account')).not.toBeNull();
    expect(explainSavedAddressIssue('address text is missing or an implausible length')).not.toBeNull();
  });

  it('does not carry driver-crosswalk messages', () => {
    expect(explainSavedAddressIssue('no Spinr driver with this phone number')).toBeNull();
  });
});
