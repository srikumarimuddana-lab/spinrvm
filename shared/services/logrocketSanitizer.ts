/**
 * LogRocket network sanitizers (PIPEDA).
 *
 * LogRocket's React Native SDK instruments fetch/XmlHttpRequest and captures
 * request AND response bodies **by default** — verified in the installed SDK
 * (@logrocket/react-native@2.3.3, dist/build.js):
 *
 *   isDisabled: false === options?.network?.isEnabled
 *            || ("ios" === Platform.OS && "native" === options?.network?.iosNetworkCaptureMode)
 *
 * With `LogRocket.init('<app>')` and no options object, both terms are false, so
 * the JS interceptor is active and unsanitised. Our API client uses `fetch`
 * (shared/api/client.ts), so every authenticated call was being shipped whole to a
 * US vendor: `POST /auth/verify-otp` carries a phone number and OTP in and access +
 * refresh tokens plus the full profile out; `GET /auth/me` carries name, email and
 * phone; ride booking carries pickup/dropoff addresses and coordinates;
 * `POST /drivers/location-batch` carries raw GPS traces; and every request carries
 * an `Authorization: Bearer` header.
 *
 * CLAUDE.md's PIPEDA section forbids raw GPS, full phone numbers, full names, email
 * addresses and exact addresses in "logs, Sentry events, or analytics payloads" —
 * session replay is squarely in that category — and shipping bearer/refresh tokens
 * to a third party is a credential-exposure problem on top of the privacy one.
 *
 * Approach: **default-deny on payloads, keep the envelope.** URL, method and status
 * survive, so "the /rides/estimate call 500'd" is still debuggable; bodies do not.
 * A denylist of sensitive endpoints was rejected deliberately — it silently fails
 * open the moment someone adds a new endpoint, which is precisely how this class of
 * bug recurs. Sensitive query parameters are masked too, because the places-search
 * calls put user-typed addresses in the query string.
 *
 * Because iosNetworkCaptureMode defaults to 'javascript' (not 'native'), these JS
 * sanitizers ARE honoured. If anyone ever sets it to 'native', the SDK bypasses
 * these functions entirely and redaction must be reconfigured natively — see the
 * SDK docs referenced in its types.d.ts.
 */

/** Shape of the request object LogRocket hands to requestSanitizer. */
export interface LogRocketRequest {
  headers: Record<string, string | null | undefined>;
  body?: string;
  method?: string;
  url?: string;
}

/** Shape of the response object LogRocket hands to responseSanitizer. */
export interface LogRocketResponse {
  headers: Record<string, string | null | undefined>;
  body?: string;
  status?: number;
  method?: string;
  url?: string;
}

/**
 * Left in place of every body so a LogRocket viewer can tell redaction happened
 * rather than concluding the request had no payload.
 */
export const REDACTED_BODY = '[body redacted client-side — Spinr PIPEDA policy]';
const REDACTED_VALUE = '[redacted]';

/**
 * Headers that carry credentials. Compared case-insensitively because header
 * casing is not guaranteed across platforms.
 */
const CREDENTIAL_HEADERS = new Set([
  'authorization',
  'cookie',
  'set-cookie',
  'x-csrf-token',
  'x-firebase-appcheck',
  'x-auth-token',
  'refresh-token',
]);

/**
 * Query parameters that can carry PII. `input`/`query`/`q` matter because the
 * places-autocomplete calls put a user-typed address there; `lat`/`lng` because
 * nearby-driver and estimate calls put coordinates there.
 */
const SENSITIVE_QUERY_PARAMS = new Set([
  'lat',
  'lng',
  'latitude',
  'longitude',
  'address',
  'origin',
  'destination',
  'input',
  'query',
  'q',
  'phone',
  'email',
  'token',
  'refresh_token',
]);

/** Redact credential headers, preserving every other header for debugging. */
export function sanitizeHeaders(
  headers: Record<string, string | null | undefined> | undefined,
): Record<string, string | null | undefined> {
  if (!headers) return {};
  const out: Record<string, string | null | undefined> = {};
  for (const [key, value] of Object.entries(headers)) {
    out[key] = CREDENTIAL_HEADERS.has(key.toLowerCase()) ? REDACTED_VALUE : value;
  }
  return out;
}

/**
 * Mask sensitive query-string values, keeping the path and the parameter NAMES so
 * the call is still identifiable.
 *
 * Deliberately string-based rather than using `URL`: React Native's URL polyfill
 * has historically been incomplete, and a throw inside a sanitizer would be worse
 * than a crude parse — LogRocket would fall back to the unsanitised value.
 */
export function sanitizeUrl(url: string | undefined): string | undefined {
  if (!url) return url;
  const split = url.indexOf('?');
  if (split === -1) return url;

  const path = url.slice(0, split);
  const rest = url.slice(split + 1);
  // Preserve any fragment rather than folding it into the last parameter value.
  const hashAt = rest.indexOf('#');
  const queryPart = hashAt === -1 ? rest : rest.slice(0, hashAt);
  const fragment = hashAt === -1 ? '' : rest.slice(hashAt);

  const masked = queryPart
    .split('&')
    .map((pair) => {
      if (!pair) return pair;
      const eq = pair.indexOf('=');
      if (eq === -1) return pair;
      const name = pair.slice(0, eq);
      return SENSITIVE_QUERY_PARAMS.has(name.toLowerCase())
        ? `${name}=${REDACTED_VALUE}`
        : pair;
    })
    .join('&');

  return `${path}?${masked}${fragment}`;
}

/**
 * requestSanitizer for LogRocket.init. Never returns null — returning null would
 * drop the record entirely, and the envelope (method, URL, status) is the part
 * worth keeping.
 */
export function sanitizeRequest(request: LogRocketRequest): LogRocketRequest {
  return {
    ...request,
    url: sanitizeUrl(request?.url),
    headers: sanitizeHeaders(request?.headers),
    // Default-deny: every request body is redacted, including ones that look
    // innocuous today. See the module docstring for why a denylist was rejected.
    body: request?.body === undefined ? undefined : REDACTED_BODY,
  };
}

/** responseSanitizer for LogRocket.init. Same contract as sanitizeRequest. */
export function sanitizeResponse(response: LogRocketResponse): LogRocketResponse {
  return {
    ...response,
    url: sanitizeUrl(response?.url),
    headers: sanitizeHeaders(response?.headers),
    body: response?.body === undefined ? undefined : REDACTED_BODY,
  };
}

/**
 * The `network` block to pass to LogRocket.init. Exported as one object so both
 * apps configure identically and neither can drift.
 */
export const logRocketNetworkConfig = {
  requestSanitizer: sanitizeRequest,
  responseSanitizer: sanitizeResponse,
};
