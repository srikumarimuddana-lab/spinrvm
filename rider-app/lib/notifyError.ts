/**
 * notifyError — the one call a catch block should make to toast an error.
 *
 * Composes the two centralised pieces:
 *   • getApiErrorMessage  → the display body (handles 429, clamps, fallback)
 *   • presentError        → title + severity from the backend error code/field
 *
 * and renders through the rider app's toast store. This is the glue layer:
 * the *decision* lives in @shared/errors/errorPresentation (pure, shared), the
 * *rendering* stays app-local (each app has a different toast engine and
 * severity vocabulary — the rider store uses 'danger' where the canonical
 * severity is 'error').
 *
 * Usage:  } catch (err) { notifyError(err, { fallbackTitle: 'Booking Failed' }); }
 */
import { getApiErrorMessage } from '@shared/api/client';
import { presentError, type ToastSeverity } from '@shared/errors/errorPresentation';
import { showToast, type ToastVariant } from '../store/toastStore';

const DEFAULT_FALLBACK_MESSAGE = 'Something went wrong. Please try again.';

// Canonical severity → rider toast variant ('error' is spelled 'danger' here).
const VARIANT: Record<ToastSeverity, ToastVariant> = {
  success: 'success',
  info: 'info',
  warning: 'warning',
  error: 'danger',
};

export interface NotifyErrorOptions {
  /** Title used when the error carries no code we map to a specific title. */
  fallbackTitle: string;
  /** Body used when the error carries no usable server message. */
  fallbackMessage?: string;
}

export function notifyError(err: unknown, opts: NotifyErrorOptions): void {
  const message = getApiErrorMessage(err, opts.fallbackMessage ?? DEFAULT_FALLBACK_MESSAGE);
  const { title, severity } = presentError(err, { fallbackTitle: opts.fallbackTitle });
  showToast(title, message, VARIANT[severity]);
}
