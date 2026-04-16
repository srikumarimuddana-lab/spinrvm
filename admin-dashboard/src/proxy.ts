import { NextRequest, NextResponse } from "next/server";

/**
 * Edge middleware — first line of defense for the admin dashboard.
 *
 * Strategy:
 *
 *   1. On login success we dual-write the JWT to sessionStorage (for React/api.ts
 *      access) AND to an `admin_token` cookie (for this middleware). The cookie
 *      is path=/ SameSite=Lax and 8-hour Max-Age (standard admin session).
 *   2. This middleware decodes the JWT and checks the `exp` claim. Expired or
 *      malformed tokens are rejected — the user is redirected to /login.
 *      Full signature verification stays on the backend (Edge Runtime cannot
 *      access the JWT_SECRET environment variable reliably across all hosts).
 *   3. Unauthenticated requests to any non-public path redirect to /login with a
 *      `next` query param so login can bounce back to the original destination.
 *   4. Public paths are explicitly listed: /login, /register/*, /track/[rideId]
 *      (rider share link), /_next/*, /api/*, and static assets.
 *
 * On logout we delete the cookie AND clear sessionStorage so both sides are in
 * lockstep — see authStore.logout().
 *
 * NOTE: renamed from `middleware` → `proxy` per Next.js 16 convention.
 */

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

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Public page — no auth required
  if (isPublic(pathname)) {
    return NextResponse.next();
  }

  const token = request.cookies.get("admin_token")?.value;

  if (token && isTokenValid(token)) {
    return NextResponse.next();
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

// Match everything except Next.js internals, /api/* routes, and static assets.
// /api/* is excluded here so the login endpoint (and all other API calls) pass
// through to the Next.js rewrite proxy without being redirected to /login.
export const config = {
  matcher: [
    "/((?!api/|_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
  ],
};
