import React from 'react';
import { Alert, Linking, Vibration } from 'react-native';
import { render, fireEvent, act, waitFor } from '@testing-library/react-native';
import { SOSButton } from '@shared/components/SOSButton';

/**
 * Regression tests for ACTION_ITEMS.md B16 (driver discreet SOS).
 *
 * `SOSButton` is shared by five screens — one driver, four rider — and design
 * sketch 010 deliberately chose a *different* interaction for riders than
 * sketch 011 chose for drivers. So the most important assertions here are the
 * ones pinning that rider behaviour did not move: `discreet` is opt-in, and
 * every existing call site passes nothing.
 *
 * The component has had no test coverage of any kind until now (only the API
 * and store layers below it were tested), so these also lock in the
 * pre-existing fail-safe properties — never report "sent" without a backend
 * 200, keep the failed state until confirmed — that the discreet path had to
 * preserve.
 */

// getSOSLocation races a real geolocation call; stub it so tests are
// deterministic and don't depend on a location provider.
jest.mock('@shared/utils/sosLocation', () => ({
  getSOSLocation: jest.fn().mockResolvedValue({ lat: 52.13, lng: -106.67 }),
}));

const HOLD_STANDARD_MS = 1200;
const HOLD_DISCREET_MS = 3000;

/** Press and hold the button for `ms`, then let the async chain settle. */
const holdFor = async (button: any, ms: number) => {
  await act(async () => {
    fireEvent(button, 'pressIn');
  });
  await act(async () => {
    jest.advanceTimersByTime(ms);
  });
  // Flush the async triggerSOS chain (location lookup + backend attempts).
  await act(async () => {
    await Promise.resolve();
  });
};

/**
 * Drain the retry loop under fake timers.
 *
 * triggerSOS awaits `new Promise(r => setTimeout(r, delay))` between attempts,
 * so each backoff needs a timer advance AND a microtask flush before the next
 * one is even scheduled. A single large advanceTimersByTime only clears the
 * first sleep — the rest have not been created yet — which leaves the button
 * stuck in "Sending" and makes the assertion look like a component bug.
 */
const drainRetries = async () => {
  for (let i = 0; i < 8; i++) {
    await act(async () => {
      jest.advanceTimersByTime(500);
      await Promise.resolve();
      await Promise.resolve();
    });
  }
};

describe('SOSButton', () => {
  let alertSpy: jest.SpyInstance;
  let openUrlSpy: jest.SpyInstance;

  beforeEach(() => {
    jest.useFakeTimers();
    alertSpy = jest.spyOn(Alert, 'alert').mockImplementation(() => {});
    openUrlSpy = jest.spyOn(Linking, 'openURL').mockResolvedValue(true as any);
    jest.spyOn(Vibration, 'vibrate').mockImplementation(() => {});
  });

  afterEach(() => {
    jest.clearAllMocks();
    jest.useRealTimers();
  });

  // ── Rider regression pins ───────────────────────────────────────────────
  // These must keep passing unchanged. If one of them fails, the discreet
  // work has leaked into the rider surface.

  it('defaults to non-discreet and still shows the native success Alert', async () => {
    const onTrigger = jest.fn().mockResolvedValue(undefined);
    const { getByLabelText } = render(<SOSButton rideId="ride-1" onTrigger={onTrigger} />);

    await holdFor(getByLabelText('Emergency SOS'), HOLD_STANDARD_MS);

    expect(onTrigger).toHaveBeenCalledWith('ride-1', 52.13, -106.67);
    await waitFor(() => expect(alertSpy).toHaveBeenCalled());
    expect(alertSpy.mock.calls[0][0]).toContain('Emergency Alert Sent');
  });

  it('still shows the native failure Alert when the backend never succeeds', async () => {
    const onTrigger = jest.fn().mockRejectedValue(new Error('network down'));
    const { getByLabelText } = render(<SOSButton rideId="ride-1" onTrigger={onTrigger} />);

    await act(async () => {
      fireEvent(getByLabelText('Emergency SOS'), 'pressIn');
    });
    await act(async () => {
      jest.advanceTimersByTime(HOLD_STANDARD_MS);
    });
    // 3 attempts with 1s/2s backoff between them.
    await drainRetries();

    await waitFor(() => expect(alertSpy).toHaveBeenCalled());
    expect(alertSpy.mock.calls[0][0]).toContain('Alert Not Sent');
  });

  // ── Discreet mode (driver) ──────────────────────────────────────────────

  it('discreet mode suppresses the native Alert and shows a silent toast instead', async () => {
    const onTrigger = jest.fn().mockResolvedValue(undefined);
    const { getByLabelText, getByText } = render(
      <SOSButton rideId="ride-1" onTrigger={onTrigger} discreet />,
    );

    await holdFor(getByLabelText('Emergency SOS'), HOLD_DISCREET_MS);

    expect(onTrigger).toHaveBeenCalled();
    // The whole point: nothing a passenger could read over the driver's
    // shoulder. No full-screen native dialog.
    expect(alertSpy).not.toHaveBeenCalled();
    expect(getByText(/Silent alert sent/i)).toBeTruthy();
  });

  it('discreet mode requires the full 3s hold — a 1.2s hold does not fire', async () => {
    const onTrigger = jest.fn().mockResolvedValue(undefined);
    const { getByLabelText } = render(
      <SOSButton rideId="ride-1" onTrigger={onTrigger} discreet />,
    );
    const button = getByLabelText('Emergency SOS');

    await act(async () => {
      fireEvent(button, 'pressIn');
    });
    await act(async () => {
      jest.advanceTimersByTime(HOLD_STANDARD_MS);
    });
    // Released before the discreet threshold — must be a no-op. The discreet
    // button gives no loud feedback while filling, so an accidental brush
    // must not be enough.
    await act(async () => {
      fireEvent(button, 'pressOut');
    });

    expect(onTrigger).not.toHaveBeenCalled();
    expect(alertSpy).not.toHaveBeenCalled();
  });

  it('discreet toast dials 911 when tapped — the only 911 path in this mode', async () => {
    const onTrigger = jest.fn().mockResolvedValue(undefined);
    const { getByLabelText, getByText } = render(
      <SOSButton rideId="ride-1" onTrigger={onTrigger} discreet />,
    );

    await holdFor(getByLabelText('Emergency SOS'), HOLD_DISCREET_MS);

    // Suppressing the native Alert removes the "Call 911" button it carried,
    // so the toast has to carry it instead or discreet mode would be a
    // safety regression dressed as a privacy fix.
    await act(async () => {
      fireEvent.press(getByText(/Silent alert sent/i));
    });
    expect(openUrlSpy).toHaveBeenCalledWith('tel:911');
  });

  it('discreet failure shows no modal but still tells the driver it did not send', async () => {
    const onTrigger = jest.fn().mockRejectedValue(new Error('network down'));
    const { getByLabelText, getByText } = render(
      <SOSButton rideId="ride-1" onTrigger={onTrigger} discreet />,
    );

    await act(async () => {
      fireEvent(getByLabelText('Emergency SOS'), 'pressIn');
    });
    await act(async () => {
      jest.advanceTimersByTime(HOLD_DISCREET_MS);
    });
    await drainRetries();

    expect(alertSpy).not.toHaveBeenCalled();
    // Discretion must not cost the driver the knowledge that help was not
    // summoned. Persistent amber state + a toast, just no dialog.
    await waitFor(() => expect(getByText(/Not sent/i)).toBeTruthy());
    expect(getByLabelText(/failed/i)).toBeTruthy();
  });

  it('discreet failure toast persists — it is the only 911 path once shown', async () => {
    const onTrigger = jest.fn().mockRejectedValue(new Error('network down'));
    const { getByLabelText, getByText } = render(
      <SOSButton rideId="ride-1" onTrigger={onTrigger} discreet />,
    );

    await act(async () => {
      fireEvent(getByLabelText('Emergency SOS'), 'pressIn');
    });
    await act(async () => {
      jest.advanceTimersByTime(HOLD_DISCREET_MS);
    });
    await drainRetries();
    await waitFor(() => expect(getByText(/Not sent/i)).toBeTruthy());

    // Well past the success-toast lifetime. A failed alert must not quietly
    // erase the driver's route to 911 — the success toast auto-clears, this
    // one deliberately does not, mirroring the persistent amber button.
    await act(async () => {
      jest.advanceTimersByTime(30000);
    });

    expect(getByText(/Not sent/i)).toBeTruthy();
    await act(async () => {
      fireEvent.press(getByText(/Not sent/i));
    });
    expect(openUrlSpy).toHaveBeenCalledWith('tel:911');
  });

  it('discreet success toast auto-clears so it does not linger on screen', async () => {
    const onTrigger = jest.fn().mockResolvedValue(undefined);
    const { getByLabelText, getByText, queryByText } = render(
      <SOSButton rideId="ride-1" onTrigger={onTrigger} discreet />,
    );

    await holdFor(getByLabelText('Emergency SOS'), HOLD_DISCREET_MS);
    expect(getByText(/Silent alert sent/i)).toBeTruthy();

    await act(async () => {
      jest.advanceTimersByTime(9000);
    });
    expect(queryByText(/Silent alert sent/i)).toBeNull();
  });

  it('discreet mode uses a short haptic, not the audible alarm pattern', async () => {
    const onTrigger = jest.fn().mockResolvedValue(undefined);
    const { getByLabelText } = render(
      <SOSButton rideId="ride-1" onTrigger={onTrigger} discreet />,
    );

    await holdFor(getByLabelText('Emergency SOS'), HOLD_DISCREET_MS);

    // The standard six-pulse array is an audible buzz in a quiet car.
    expect(Vibration.vibrate).toHaveBeenCalledWith(40);
  });

  // ── Fail-safe properties that predate this change ───────────────────────

  it('never reports success when there is no active ride', async () => {
    const onTrigger = jest.fn().mockResolvedValue(undefined);
    const { getByLabelText } = render(<SOSButton onTrigger={onTrigger} discreet />);

    await holdFor(getByLabelText('Emergency SOS'), HOLD_DISCREET_MS);

    expect(onTrigger).not.toHaveBeenCalled();
    // Deliberately still a native Alert even in discreet mode: nothing was
    // sent and nothing will be, so calling 911 directly is the only useful
    // action and it is worth interrupting for.
    await waitFor(() => expect(alertSpy).toHaveBeenCalled());
    expect(alertSpy.mock.calls[0][0]).toContain('No Active Ride');
  });
});
