/**
 * Extract safe, actionable diagnostics from a native App Check failure.
 *
 * Native provider errors can contain nested fields. Keep this intentionally
 * small and bounded: it is used in production logs/Crashlytics and must never
 * include an App Check token or the complete native error object.
 */
export function formatAppCheckError(error: unknown): string {
  if (error instanceof Error) {
    return JSON.stringify({
      code: getStringField(error, 'code'),
      message: truncate(error.message),
      nativeError: getStringField(error, 'nativeError'),
    });
  }

  if (typeof error === 'object' && error !== null) {
    const value = error as Record<string, unknown>;
    return JSON.stringify({
      code: toSafeString(value.code),
      message: truncate(toSafeString(value.message)),
      nativeError: toSafeString(value.nativeError),
    });
  }

  return JSON.stringify({ message: truncate(String(error)) });
}

function getStringField(error: Error, field: string): string | undefined {
  return toSafeString((error as Error & Record<string, unknown>)[field]);
}

function toSafeString(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined;
}

function truncate(value: string | undefined): string | undefined {
  if (!value) return undefined;
  return value.length > 500 ? value.slice(0, 500) + '…' : value;
}
