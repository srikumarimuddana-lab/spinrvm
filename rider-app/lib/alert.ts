import { Alert, type AlertButton } from 'react-native';
import type { ExtractedError } from '@shared/api/client';
import { tKey } from '../i18n';

/**
 * Options for `showErrorAlert`.
 *
 * Pass an `error` (a `SpinrApiError`, an `ExtractedError`, or any
 * exception/string) and the helper will resolve the most user-friendly
 * message available — preferring the i18n entry for `messageKey`, then
 * the backend's English `message`, then the generic `errors.unknown` key.
 */
export interface ShowAlertOptions {
  /** Title key (looked up via i18n) or literal string. Defaults to `common.error`. */
  title?: string;
  /** Either a SpinrApiError, ExtractedError, plain Error, or a raw message string. */
  error?: unknown;
  /** Override the action hint shown after the body. */
  actionHint?: string;
  /** Optional buttons to render. Defaults to a single OK. */
  buttons?: AlertButton[];
}

interface MaybeStructured {
  messageKey?: string;
  message?: string;
  actionHint?: string;
}

function isStructured(value: unknown): value is MaybeStructured {
  return typeof value === 'object' && value !== null;
}

/**
 * Render a localised error alert.
 *
 * Resolution order for the body text:
 *   1. `error.messageKey` → i18n lookup (with `error.message` as fallback)
 *   2. `error.message` (plain string from backend or thrown Error)
 *   3. The string itself, if `error` is a string
 *   4. `errors.unknown` i18n key
 *
 * The action hint, if present, is appended after a blank line.
 */
export function showErrorAlert(opts: ShowAlertOptions = {}): void {
  const titleSource = opts.title ?? 'common.error';
  const title = tKey(titleSource, titleSource === 'common.error' ? 'Error' : titleSource);

  let body: string;
  let hint = opts.actionHint;

  const e = opts.error;
  const unknownFallback = tKey('errors.unknown', 'Something went wrong. Please try again.');

  if (typeof e === 'string') {
    body = e;
  } else if (isStructured(e)) {
    const structured = e as MaybeStructured & Partial<ExtractedError>;
    if (structured.messageKey) {
      body = tKey(structured.messageKey, structured.message ?? unknownFallback);
    } else if (structured.message) {
      body = structured.message;
    } else {
      body = unknownFallback;
    }
    hint = hint ?? structured.actionHint;
  } else {
    body = unknownFallback;
  }

  if (hint) {
    body = `${body}\n\n${hint}`;
  }

  Alert.alert(title, body, opts.buttons);
}
