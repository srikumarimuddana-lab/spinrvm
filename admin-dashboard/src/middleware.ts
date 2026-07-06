import { NextRequest, NextResponse } from "next/server";
import { isTrackingHost, TRACK_CSP } from "@/lib/track-host";

/**
 * Edge middleware — first line of defense, and host-based router.
 *
 * The same Next.js app is served on two domains:
 *
 *   - admin-spinr.spinr.ca  → full admin panel (this middleware enforces auth)
 *   - track.spinr.ca        → ONLY the public read-only tracking page, on
 *                             clean URLs: track.spinr.ca/{token} is rewritten
 *                             to /track/{token}. Every admin route 404s here so
 *                             the admin surface is never exposed to customers.
 *
 * Admin-domain strategy:
 *
 *   1. On login success the access token is dual-written: held in Zustand memory
 *      (for React/api.ts) AND written to an HttpOnly `admin_token` cookie via
 *      /api/auth/set-cookie (for this middleware, which cannot read JS memory).
 *      The token is NOT stored in sessionStorage — only the non-sensitive user
 *      profile (`user`, `isAuthenticated`) is persisted there via Zustand's
 *      `partialize`. SameSite=Strict, HttpOnly, Secure-in-production.
 *   2. This middleware decodes the JWT and checks the `exp` claim. Expired or
 *      malformed tokens are rejected — the user is redirected to /login.
 *      Full signature verification stays on the backend (Edge Runtime cannot
 *      access the JWT_SECRET environment variable reliably across all hosts).
 *   3. Unauthenticated requests to any non-public path redirect to /login with a
 *      `next` query param so login can bounce back to the original destination.
 *   4. Public paths are explicitly listed: /login, /register/*, /track/[rideId]
 *      (rider share link), /_next/*, /api/*, and static assets.
 *
 * On logout the HttpOnly cookie is deleted via DELETE /api/auth/set-cookie and
 * the Zustand store is reset (user=null, isAuthenticated=false) — see authStore.logout().
 *
 * CSP (F-05):
 *   A fresh random nonce is generated per request and embedded in the
 *   Content-Security-Policy header. The nonce is forwarded to the app via the
 *   `x-nonce` request header so the root layout can pass it to Next.js's
 *   hydration scripts. `unsafe-inline` is removed from script-src;
 *   `unsafe-eval` is removed entirely. `style-src 'unsafe-inline'` is kept
 *   because React inline-style props and several third-party libraries (Recharts,
 *   Leaflet, Mapbox) emit inline style attributes that cannot carry nonces.
 */

// ── Nonce helpers ─────────────────────────────────────────────────────────────

function generateNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes));
}

function buildCsp(nonce: string): string {
  const isDev = process.env.NODE_ENV === "development";
  return [
    "default-src 'self'",
    // nonce replaces unsafe-inline; strict-dynamic lets Next.js load its chunks.
    // unsafe-eval is required in dev mode only: React/Turbopack uses eval() for
    // source-map reconstruction and call-stack rebuilding. Never emitted in prod.
    `script-src 'nonce-${nonce}' 'strict-dynamic' https:${isDev ? " 'unsafe-eval'" : ""}`,
    // unsafe-inline kept for styles: React style props + Recharts/Leaflet/Mapbox
    // emit inline style= attributes which cannot carry nonces
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob: https:",
    "font-src 'self'",
    "connect-src 'self' https: wss: ws:",
    // MapLibre GL v5 creates its tile-processing Web Worker from a blob: URL.
    // Chrome does not extend 'self' to cover blob: workers even when the blob
    // is same-origin — it requires blob: explicitly here. Without this the
    // worker is blocked and the map canvas stays blank.
    "worker-src blob: 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}

// ── IP allowlist ───────────────────────────────────────────────────────────────

// IP allowlist (F-06): set ADMIN_ALLOWED_IPS to a comma-separated list of
// exact IPs or network prefixes (e.g. "10.0.0.,192.168.1.100").
// Leave unset or empty to disable the check (development / cloud deployments
// where IPs are dynamic). An IP is allowed if any entry in the list is a
// prefix of the client IP — exact match ("1.2.3.4") works as a strict subset
// of prefix match ("1.2.3.4" starts with "1.2.3.4").
function buildAllowedIpEntries(): string[] {
  const raw = process.env.ADMIN_ALLOWED_IPS ?? "";
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

const ALLOWED_IP_ENTRIES = buildAllowedIpEntries();

function isIpAllowed(request: NextRequest): boolean {
  if (ALLOWED_IP_ENTRIES.length === 0) return true;
  const forwarded = request.headers.get("x-forwarded-for");
  const realIp = request.headers.get("x-real-ip") ?? "";
  const clientIp = (forwarded ? forwarded.split(",")[0] : realIp).trim();
  return ALLOWED_IP_ENTRIES.some((entry) => clientIp.startsWith(entry));
}

// /company-login is handled explicitly before the admin IP allowlist (see
// middleware()) so it stays reachable for external company users — it does
// not go through isPublic().
const PUBLIC_PATHS = ["/login"];
const PUBLIC_PREFIXES = ["/register/", "/track/"];

function isPublic(pathname: string): boolean {
  if (PUBLIC_PATHS.includes(pathname)) return true;
  return PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

/**
 * Decode a JWT's payload without verifying the signature (Edge Runtime
 * compatible). Returns null if the token is malformed.
 */
function decodeJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    const payload = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const json = atob(payload);
    return JSON.parse(json);
  } catch {
    return null;
  }
}

/**
 * Check whether the JWT has a valid structure and has not expired.
 * Returns true only if the token has a future `exp` claim.
 */
function isTokenValid(token: string): boolean {
  const payload = decodeJwtPayload(token);
  if (!payload) return false;
  const exp = payload.exp;
  if (typeof exp !== "number") return false;
  // Reject if expired (with 30-second leeway for clock skew)
  return exp * 1000 > Date.now() - 30_000;
}

function passThroughWithNonce(request: NextRequest, nonce: string): NextResponse {
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", buildCsp(nonce));
  return response;
}

// ── Tracking-domain router ───────────────────────────────────────────────────
// On track.spinr.ca the ONLY thing served is the public read-only tracking
// page. Customers get clean links — track.spinr.ca/{token} — which we rewrite
// to the /track/[rideId] route. Any other path (admin login, dashboard, a bare
// root with no token) returns 404 so the admin surface is never reachable here.
function handleTrackingHost(request: NextRequest): NextResponse {
  const { pathname } = request.nextUrl;

  // Already a canonical /track/... path: serve it directly with the public CSP.
  // (The matcher normally excludes /track/, so this branch is belt-and-braces.)
  if (pathname === "/track" || pathname.startsWith("/track/")) {
    const response = NextResponse.next();
    response.headers.set("Content-Security-Policy", TRACK_CSP);
    return response;
  }

  // A single path segment is the share token: /{token} → /track/{token}.
  const segment = pathname.replace(/^\/+/, "");
  if (segment.length > 0 && !segment.includes("/")) {
    const url = request.nextUrl.clone();
    url.pathname = `/track/${segment}`;
    const response = NextResponse.rewrite(url);
    response.headers.set("Content-Security-Policy", TRACK_CSP);
    return response;
  }

  // Root, or any multi-segment / admin path on the tracking domain → not found.
  return new NextResponse("Not found", { status: 404 });
}

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // ── Host-based routing ──────────────────────────────────────────────────────
  // The tracking domain serves only the public page and never the admin panel.
  const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
  if (isTrackingHost(host)) {
    return handleTrackingHost(request);
  }

  const nonce = generateNonce();

  // ── Company portal (Spinr for Business) — CUSTOMER-facing surface ───────────
  // Handled BEFORE the admin IP allowlist: company employees are external
  // users who must reach /company-login and /company-portal even when
  // ADMIN_ALLOWED_IPS restricts the STAFF admin surface. Company users hold
  // RIDER tokens (phone OTP), gated on their own company_token cookie, and
  // bounce to /company-login — never the staff /login.
  if (pathname === "/company-login") {
    return passThroughWithNonce(request, nonce);
  }
  if (pathname === "/company-portal" || pathname.startsWith("/company-portal/")) {
    const companyToken = request.cookies.get("company_token")?.value;
    if (companyToken && isTokenValid(companyToken)) {
      return passThroughWithNonce(request, nonce);
    }
    // The access token (company_token) expires in 15 min, but the refresh
    // session lasts 30 days. When the access token is stale/absent but the
    // company_session marker shows a refreshable session exists, load the shell
    // so the client can silentRefresh — otherwise an idle reload would force a
    // needless re-login. If the refresh fails, the client layout redirects.
    const hasRefreshableSession = request.cookies.get("company_session")?.value === "1";
    if (hasRefreshableSession) {
      return passThroughWithNonce(request, nonce);
    }
    const companyRedirect = request.nextUrl.clone();
    companyRedirect.pathname = "/company-login";
    companyRedirect.searchParams.set("next", pathname);
    const companyResponse = NextResponse.redirect(companyRedirect);
    if (companyToken) {
      companyResponse.cookies.set("company_token", "", { path: "/", maxAge: 0 });
    }
    return companyResponse;
  }

  // ── Admin domain below ──────────────────────────────────────────────────────
  // IP allowlist check — runs before auth so blocked IPs see 403, not /login.
  // (The company surface above is intentionally exempt — see comment there.)
  if (!isIpAllowed(request)) {
    return new NextResponse("Forbidden", { status: 403 });
  }

  // Public page — no auth required
  if (isPublic(pathname)) {
    return passThroughWithNonce(request, nonce);
  }

  const token = request.cookies.get("admin_token")?.value;

  if (token && isTokenValid(token)) {
    return passThroughWithNonce(request, nonce);
  }

  // No cookie or expired/malformed token → bounce to /login
  const redirectUrl = request.nextUrl.clone();
  redirectUrl.pathname = "/login";
  if (pathname !== "/" && pathname !== "/login") {
    redirectUrl.searchParams.set("next", pathname);
  }
  const response = NextResponse.redirect(redirectUrl);

  // Clear the stale cookie so the user isn't stuck in a redirect loop
  if (token) {
    response.cookies.set("admin_token", "", { path: "/", maxAge: 0 });
  }

  return response;
}

// Match everything except:
//   - Next.js internals and static assets
//   - /api/* routes (login endpoint must not redirect to /login)
//   - /track/* routes (public share-link pages; have their own static CSP
//     in next.config.ts and must never be bounced to the admin login screen)
export const config = {
  matcher: [
    "/((?!api/|track/|_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
  ],
};
