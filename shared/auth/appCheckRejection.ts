/**
 * Firebase App Check runs in front of /auth/refresh and answers 401
 * {"detail":"App Check token required"} or "Invalid App Check token"
 * before the refresh token is examined. That is not a dead login.
 * A real rejection is TokenExpiredException, error code 1003.
 */
export function isAppCheckRejection(body: unknown): boolean {
  if (!body || typeof body !== 'object') return false;
  const detail = (body as { detail?: unknown }).detail;
  return typeof detail === 'string' && /app check token/i.test(detail);
}

/** Body from a raw fetch Response or from SpinrApiError's `.response.data`. */
export async function rejectionBody(error: unknown): Promise<unknown> {
  if (!error || typeof error !== 'object') return null;
  const err = error as {
    data?: unknown;
    response?: { data?: unknown };
    json?: () => Promise<unknown>;
  };
  if (err.response && err.response.data !== undefined) return err.response.data;
  if (typeof err.json !== 'function') return err.data !== undefined ? err.data : null;
  try {
    return await err.json();
  } catch {
    return null;
  }
}
