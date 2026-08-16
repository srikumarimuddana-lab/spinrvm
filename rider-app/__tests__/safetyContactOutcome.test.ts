/**
 * deriveContactOutcome — the guard that stopped SOS from claiming a
 * notification it could not confirm (analysis finding S1).
 *
 * Before this, SOSButton asserted "your location has been shared with Spinr
 * support and your emergency contacts" on ANY backend 200 — including when
 * every Twilio send failed, and when the rider had no contacts saved at all.
 */
import { deriveContactOutcome, type SOSTriggerResult } from '@shared/types/safety';

const base: SOSTriggerResult = {
  success: true,
  incident_id: 'inc-1',
  contacts_notified: 0,
  contacts: [],
};

describe('deriveContactOutcome', () => {
  it('reports success only when at least one contact was actually reached', () => {
    expect(
      deriveContactOutcome({
        ...base,
        contacts_notified: 1,
        contacts: [
          { id: 'c1', name: 'Jane', notified: true },
          { id: 'c2', name: 'Sam', notified: false },
        ],
      }),
    ).toBe('contacts_notified');
  });

  it('reports failure when contacts exist but none were reached', () => {
    expect(
      deriveContactOutcome({
        ...base,
        contacts_notified: 0,
        contacts: [{ id: 'c1', name: 'Jane', notified: false }],
      }),
    ).toBe('contacts_failed');
  });

  it('distinguishes "no contacts saved" from "contacts failed"', () => {
    expect(deriveContactOutcome({ ...base, contacts: [] })).toBe('no_contacts');
  });

  it('treats notification_warning as failure even if contacts[] is empty', () => {
    // The backend returns 200 with this warning when the whole contact block
    // threw — the incident persisted and the safety team was alerted, but no
    // contact was reached. Must not read as "no contacts saved".
    expect(
      deriveContactOutcome({
        ...base,
        contacts: [],
        notification_warning: 'Emergency contacts could not be reached',
      }),
    ).toBe('contacts_failed');
  });

  it('reports unknown for a deduplicated replay, not "no contacts"', () => {
    // A replay performs no new sends and cannot report on the original one,
    // so contacts[] is empty for a reason that has nothing to do with the
    // rider's saved contacts. Claiming "you have none saved" would be wrong.
    expect(
      deriveContactOutcome({ ...base, duplicate: true, contacts: [] }),
    ).toBe('unknown');
  });

  it('reports unknown — never success — for a caller that returns nothing', () => {
    expect(deriveContactOutcome(undefined)).toBe('unknown');
    expect(deriveContactOutcome(null)).toBe('unknown');
  });
});
