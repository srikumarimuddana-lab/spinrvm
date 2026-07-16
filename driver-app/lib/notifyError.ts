/**
 * notifyError — the one call a catch block should make to toast an error.
 *
 * Composes the two centralised pieces:
 *   • getApiErrorMessage  → the display body (handles 429, clamps, fallback)
 *   • presentError        → title + severity from the backend error code/field
 *
 * and renders through the driver app's toast hook. This is the glue layer: the
 * *decision* lives in @shared/errors/errorPresentation (pure, shared), the
 * *rendering* stays app-local. The driver toast type vocabulary already
 * matches the canonical severity names, so the mapping is identity.
 *
 * Usage:  } catch (err) { notifyError(err, { fallbackTitle: 'Cannot Go Online' }); }
 */
import { getApiErrorMessage } from '@shared/api/client';
import { presentError, type ToastSeverity } from '@shared/errors/errorPresentation';
import { showToast, type ToastType } from '../hooks/useToast';

const DEFAULT_FALLBACK_MESSAGE = 'Something went wrong. Please try again.';

// Canonical severity → driver toast type (identity — same vocabulary).
const TYPE: Record<ToastSeverity, ToastType> = {
  success: 'success',
  info: 'info',
  warning: 'warning',
  error: 'error',
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
  showToast(TYPE[severity], title, message);
}
