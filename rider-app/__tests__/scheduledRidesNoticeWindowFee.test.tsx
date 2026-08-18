import fs from 'node:fs';
import path from 'node:path';

/**
 * Regression test for ACTION_ITEMS.md C29 — the scheduled-ride cancel
 * confirmation must warn the rider about a possible notice-window
 * cancellation fee *before* they confirm, not only after the charge lands.
 *
 * backend/routes/rides/queries.py::get_scheduled_rides() attaches a
 * server-computed `notice_window_fee_amount` to a ride only when
 * scheduled_ride_notice_window_fee_enabled is on AND cancelling that ride
 * right now would actually trigger the fee (see
 * backend/tests/test_scheduled_cancel_notice_fee.py and the new
 * TestGetScheduledRidesNoticeWindowFeePreview class in
 * backend/tests/test_p2_scheduled_rides.py for that coverage).
 *
 * Note on test approach: a full react-test-renderer mount of this screen
 * (matching bookingProposalCardPromo.test.tsx / privacySettingsToggles.test.tsx)
 * was attempted first, but this screen's FlatList → VirtualizedList tree
 * schedules native cell-render timers that never settle under Jest's
 * environment even with react-native fully mocked out and FlatList/
 * RefreshControl stubbed — the test hangs past any reasonable timeout. No
 * pre-existing test file for app/scheduled-rides.tsx exists in this repo,
 * and jest.config.js documents the general policy this gap falls under:
 * "app screens are covered by e2e" (coverageThreshold comment) rather than
 * component-render Jest tests. Forcing a flaky/hanging render test through
 * would be worse than admitting the gap, so this test instead pins the
 * exact source contract of handleCancel's message-building logic — the same
 * technique this repo already uses for other screens it doesn't
 * component-render-test (see ride-details-route.test.tsx). This is real
 * regression coverage (it will fail if the conditional, the field name, or
 * the fallback text is edited) but it is NOT a rendered-DOM assertion —
 * stated explicitly per CLAUDE.md rather than implying full coverage.
 */
const source = fs.readFileSync(path.join(__dirname, '..', 'app', 'scheduled-rides.tsx'), 'utf8');

describe('scheduled-rides cancel confirmation — notice-window fee warning (C29)', () => {
  it('reads the server-computed notice_window_fee_amount for the ride being cancelled', () => {
    expect(source).toContain(
      "const ride = scheduledRides.find((r: any) => r.id === rideId) as any;",
    );
    expect(source).toContain('const feeAmount = ride?.notice_window_fee_amount;');
  });

  it('builds a fee-warning message only when feeAmount is present, otherwise keeps the original generic text unchanged', () => {
    expect(source).toContain(
      "? `Are you sure you want to cancel this scheduled ride? A $${Number(feeAmount).toFixed(2)} late-cancellation fee may apply.`",
    );
    expect(source).toContain(": 'Are you sure you want to cancel this scheduled ride?';");
  });

  it('passes the computed message into the ConfirmSheet state instead of the old hardcoded string', () => {
    expect(source).toContain('message,');
    // The old unconditional literal must be gone from the confirmSheet call
    // site — it now only appears as the no-fee fallback branch of the
    // ternary asserted above.
    const setConfirmSheetCallSite = source.slice(source.indexOf('setConfirmSheet({'));
    expect(setConfirmSheetCallSite.slice(0, 200)).not.toContain(
      "message: 'Are you sure you want to cancel this scheduled ride?'",
    );
  });
});
