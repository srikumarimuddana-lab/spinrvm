/**
 * toastStore dedupe guardrail
 *
 * A single user action can raise the same toast from more than one code path
 * (e.g. a ride cancel handled by both the local button and the server's WS
 * echo). Each show() used to mint a new id, which restarted the banner's enter
 * animation — a visible flicker. show() now collapses an identical toast fired
 * within DEDUPE_WINDOW_MS onto the same id (extending its timer) and only mints
 * a fresh id for new content or a deliberate repeat after the window.
 *
 * Code under test: rider-app/store/toastStore.ts::show
 */

import { useToastStore, showToast } from '../toastStore';

beforeEach(() => {
  useToastStore.setState({ current: null });
  jest.restoreAllMocks();
});

describe('toastStore — dedupe', () => {
  it('keeps the same id for an identical toast fired within the window', () => {
    const nowSpy = jest.spyOn(Date, 'now');
    nowSpy.mockReturnValue(1000);
    showToast('Ride Cancelled', 'Your ride has been cancelled.', 'warning');
    const firstId = useToastStore.getState().current?.id;

    nowSpy.mockReturnValue(1300); // +300ms, inside the 1s window
    showToast('Ride Cancelled', 'Your ride has been cancelled.', 'warning');

    const after = useToastStore.getState().current;
    expect(after?.id).toBe(firstId);      // no re-animate
    expect(after?.shownAt).toBe(1300);    // timer refreshed
  });

  it('mints a new id when content differs', () => {
    showToast('Ride Cancelled', 'Your ride has been cancelled.', 'warning');
    const firstId = useToastStore.getState().current?.id;

    showToast('Driver Unavailable', 'Finding another driver…', 'info');

    expect(useToastStore.getState().current?.id).not.toBe(firstId);
    expect(useToastStore.getState().current?.title).toBe('Driver Unavailable');
  });

  it('mints a new id for an identical toast repeated after the window', () => {
    const nowSpy = jest.spyOn(Date, 'now');
    nowSpy.mockReturnValue(1000);
    showToast('Code Sent', 'A new code has been sent.', 'success');
    const firstId = useToastStore.getState().current?.id;

    nowSpy.mockReturnValue(6000); // +5s, deliberate retry outside the window
    showToast('Code Sent', 'A new code has been sent.', 'success');

    expect(useToastStore.getState().current?.id).not.toBe(firstId);
  });

  it('preserves a custom duration when a deduped repeat omits one', () => {
    const nowSpy = jest.spyOn(Date, 'now');
    nowSpy.mockReturnValue(1000);
    showToast('Offline Actions Lost', 'Some queued actions could not be recovered.', 'warning', 6000);
    expect(useToastStore.getState().current?.duration).toBe(6000);

    nowSpy.mockReturnValue(1300); // identical content within window, no duration
    showToast('Offline Actions Lost', 'Some queued actions could not be recovered.', 'warning');

    // The deliberately-longer banner must not be shortened to the default.
    expect(useToastStore.getState().current?.duration).toBe(6000);
  });

  it('dismiss clears the current toast', () => {
    showToast('Hello', 'World', 'info');
    expect(useToastStore.getState().current).not.toBeNull();
    useToastStore.getState().dismiss();
    expect(useToastStore.getState().current).toBeNull();
  });
});
