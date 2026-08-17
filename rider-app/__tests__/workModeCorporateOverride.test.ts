/**
 * Work Mode must not override a rider's explicit payment choice.
 *
 * The defect: both booking screens sync Work Mode's corporate-billing default
 * in a `useEffect` that re-runs when `activeCompanyId` / the corporate-account
 * list changes — which a `fetchWorkProfiles()` resolving shortly after mount
 * does. So a rider could deliberately select their personal card, have the
 * profile fetch land a moment later, and watch the selection silently revert
 * to corporate. The ride was then billed to the COMPANY for a personal trip,
 * with neither party notified.
 *
 * This is the only defect in the heatmap pre-deploy review that moves real
 * money, and nothing pinned it — hence a behavioural suite over the decision
 * itself, plus a source contract that both screens actually consult it and
 * mark every selection point.
 */

import fs from 'fs';
import path from 'path';

import { shouldApplyWorkModeDefault } from '../utils/workModeDefault';

describe('shouldApplyWorkModeDefault', () => {
  const base = { workModeEnabled: true, hasCorporateAccounts: true, riderChosePayment: false };

  it('applies the corporate default for a work-mode rider who has not chosen', () => {
    // This is the point of Work Mode — the guard must not break it.
    expect(shouldApplyWorkModeDefault(base)).toBe(true);
  });

  it('does NOT re-apply after the rider explicitly chose a payment source', () => {
    // The money bug: a late profile fetch re-running the effect must not
    // move billing back to the company.
    expect(shouldApplyWorkModeDefault({ ...base, riderChosePayment: true })).toBe(false);
  });

  it('stays suppressed across repeated effect re-runs after a choice', () => {
    // The effect fires once per dependency change, not once total — a guard
    // that only held for the first re-run would still lose the rider's choice.
    const afterChoice = { ...base, riderChosePayment: true };
    for (let i = 0; i < 5; i++) {
      expect(shouldApplyWorkModeDefault(afterChoice)).toBe(false);
    }
  });

  it('never applies when work mode is off', () => {
    expect(shouldApplyWorkModeDefault({ ...base, workModeEnabled: false })).toBe(false);
    expect(
      shouldApplyWorkModeDefault({ ...base, workModeEnabled: false, riderChosePayment: true }),
    ).toBe(false);
  });

  it('never applies when the rider has no corporate account to bill', () => {
    // Otherwise the screen would switch to corporate billing with no company
    // selected, and booking would fail at settlement.
    expect(shouldApplyWorkModeDefault({ ...base, hasCorporateAccounts: false })).toBe(false);
  });

  it('honours an explicit choice of corporate too, not just personal', () => {
    // The guard is about "the rider decided", not "the rider decided against
    // us" — choosing corporate by hand is equally explicit and must not be
    // re-derived on the next effect run.
    expect(
      shouldApplyWorkModeDefault({ ...base, riderChosePayment: true, hasCorporateAccounts: true }),
    ).toBe(false);
  });
});

/**
 * Source contract. These screens are ~1500-line components wired to Stripe,
 * expo-router, maps and several stores; the file's existing tests use this
 * same source-assertion approach because rendering them in jest is not
 * practical. The behavioural suite above covers the decision — this pins that
 * both screens actually route through it and mark every place a rider picks a
 * payment source, which is the half a pure-function test cannot see.
 */
describe.each([
  ['payment-confirm.tsx', 'app/payment-confirm.tsx'],
  ['ride-options.tsx', 'app/ride-options.tsx'],
])('%s work-mode override contract', (_label, relPath) => {
  const source = fs.readFileSync(path.resolve(__dirname, '..', relPath), 'utf8');

  it('gates the work-mode sync on the shared decision helper', () => {
    expect(source).toContain('shouldApplyWorkModeDefault');
    // The raw condition must not survive alongside it — that was the bug.
    expect(source).not.toMatch(/if \(workModeEnabled && corporateAccounts\.length > 0\) \{/);
  });

  it('passes the rider-choice ref into the decision', () => {
    expect(source).toMatch(/riderChosePayment:\s*riderChosePaymentRef\.current/);
  });

  it('marks a rider choice at every point that sets the payment source', () => {
    // Every setUseCorporate call is either the effect's own default or a
    // rider action; each rider action must set the ref, or that path silently
    // stays overridable.
    const riderActions = source
      .split('\n')
      .filter((line) => /setUseCorporate\(/.test(line))
      .filter((line) => /onPress=|onValueChange=/.test(line));

    expect(riderActions.length).toBeGreaterThan(0);
    for (const line of riderActions) {
      expect(line).toContain('riderChosePaymentRef.current = true');
    }
  });
});
