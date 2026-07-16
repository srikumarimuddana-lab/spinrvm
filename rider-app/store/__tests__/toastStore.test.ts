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
import { TOAST_MESSAGE_MAX, TOAST_TITLE_MAX } from '@shared/utils/toastMessage';

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

describe('toastStore — length caps', () => {
  it('clamps an over-long message to TOAST_MESSAGE_MAX at a word boundary', () => {
    const longMessage = 'word '.repeat(60).trim(); // 299 chars
    showToast('Booking Failed', longMessage, 'danger');
    const shown = useToastStore.getState().current!;
    expect(shown.message!.length).toBeLessThanOrEqual(TOAST_MESSAGE_MAX);
    expect(shown.message!.endsWith('…')).toBe(true);
    expect(shown.message).not.toMatch(/\swor…$/); // no mid-word cut
  });

  it('clamps an over-long title to TOAST_TITLE_MAX', () => {
    const longTitle = 'This title is far too long to be a title and reads like a message body instead';
    showToast(longTitle, 'Short message.', 'warning');
    const shown = useToastStore.getState().current!;
    expect(shown.title.length).toBeLessThanOrEqual(TOAST_TITLE_MAX);
    expect(shown.title.endsWith('…')).toBe(true);
  });

  it('leaves short content untouched and keeps undefined message undefined', () => {
    showToast('Code Sent', undefined, 'success');
    const shown = useToastStore.getState().current!;
    expect(shown.title).toBe('Code Sent');
    expect(shown.message).toBeUndefined();
  });

  it('dedupes a repeat of the same over-long toast (clamp happens before compare)', () => {
    const longMessage = 'repeat '.repeat(40).trim();
    const nowSpy = jest.spyOn(Date, 'now');
    nowSpy.mockReturnValue(1000);
    showToast('Sync Failed', longMessage, 'danger');
    const firstId = useToastStore.getState().current?.id;

    nowSpy.mockReturnValue(1300); // inside the dedupe window
    showToast('Sync Failed', longMessage, 'danger');

    expect(useToastStore.getState().current?.id).toBe(firstId);
  });
});
