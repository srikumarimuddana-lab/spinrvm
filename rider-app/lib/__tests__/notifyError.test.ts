/**
 * notifyError — the glue layer composing getApiErrorMessage (body) +
 * presentError (title/severity) into a rider toast. Pins the severity→
 * variant remap this file exists for ('error' → 'danger', everything else
 * passes through unchanged) and that both fallbacks are threaded correctly.
 */
import { notifyError } from '../notifyError';
import { getApiErrorMessage } from '@shared/api/client';
import { presentError } from '@shared/errors/errorPresentation';
import { showToast } from '../../store/toastStore';

jest.mock('@shared/api/client', () => ({
  getApiErrorMessage: jest.fn(),
}));
jest.mock('@shared/errors/errorPresentation', () => ({
  presentError: jest.fn(),
}));
jest.mock('../../store/toastStore', () => ({
  showToast: jest.fn(),
}));

const mockGetApiErrorMessage = getApiErrorMessage as jest.Mock;
const mockPresentError = presentError as jest.Mock;
const mockShowToast = showToast as jest.Mock;

describe('notifyError', () => {
  beforeEach(() => jest.clearAllMocks());

  it('composes the message and title/severity into a single toast call', () => {
    mockGetApiErrorMessage.mockReturnValue('Card declined');
    mockPresentError.mockReturnValue({ title: 'Payment Failed', severity: 'error' });

    notifyError(new Error('boom'), { fallbackTitle: 'Booking Failed' });

    expect(mockGetApiErrorMessage).toHaveBeenCalledWith(
      expect.any(Error),
      'Something went wrong. Please try again.',
    );
    expect(mockPresentError).toHaveBeenCalledWith(expect.any(Error), {
      fallbackTitle: 'Booking Failed',
    });
    // 'error' severity remaps to the rider toast's 'danger' variant.
    expect(mockShowToast).toHaveBeenCalledWith('Payment Failed', 'Card declined', 'danger');
  });

  it.each([
    ['success', 'success'],
    ['info', 'info'],
    ['warning', 'warning'],
  ] as const)('passes non-error severity %s through unchanged as %s', (severity, expectedVariant) => {
    mockGetApiErrorMessage.mockReturnValue('msg');
    mockPresentError.mockReturnValue({ title: 'Title', severity });

    notifyError('x', { fallbackTitle: 'Fallback' });

    expect(mockShowToast).toHaveBeenCalledWith('Title', 'msg', expectedVariant);
  });

  it('uses the caller-supplied fallbackMessage over the module default', () => {
    mockGetApiErrorMessage.mockReturnValue('resolved');
    mockPresentError.mockReturnValue({ title: 'T', severity: 'info' });

    notifyError(undefined, { fallbackTitle: 'F', fallbackMessage: 'Custom fallback' });

    expect(mockGetApiErrorMessage).toHaveBeenCalledWith(undefined, 'Custom fallback');
  });

  it('defaults to the module fallback message when none is supplied', () => {
    mockGetApiErrorMessage.mockReturnValue('resolved');
    mockPresentError.mockReturnValue({ title: 'T', severity: 'info' });

    notifyError(undefined, { fallbackTitle: 'F' });

    expect(mockGetApiErrorMessage).toHaveBeenCalledWith(
      undefined,
      'Something went wrong. Please try again.',
    );
  });
});
