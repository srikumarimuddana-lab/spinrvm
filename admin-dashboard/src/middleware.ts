import { NextRequest, NextResponse } from "next/server";

/**
 * Edge middleware — first line of defense for the admin dashboard.
 *
 * Previously the dashboard was gated client-side only (src/app/dashboard/layout.tsx
 * redirects if the Zustand store has no token). That leaks HTML: unauthenticated
 * users saw the dashboard skeleton for one render before the effect fired.
 *
 * Strategy:
 *
 *   1. On login success we dual-write the JWT to localStorage (for React/api.ts
 *      access) AND to an `admin_token` cookie (for this middleware). The cookie
 *      is path=/ SameSite=Lax and 30-day Max-Age, matching the JWT expiry.
 *   2. This middleware only checks for the COOKIE'S PRESENCE — it never validates
 *      the JWT signature. That stays on the client + backend.
 *   3. Unauthenticated requests to any non-public path redirect to /login with a
 *      `next` query param so login can bounce back to the original destination.
 *   4. Public paths are explicitly listed: /login, /register/*, /track/[rideId]
 *      (rider share link), /_next/*, /api/*, and static assets.
 *
 * On logout we delete the cookie AND clear localStorage so both sides are in
 * lockstep — see authStore.logout().
 */

const PUBLIC_PATHS = ["/login"];
const PUBLIC_PREFIXES = ["/register/", "/track/"];

function isPublic(pathname: string): boolean {
  if (PUBLIC_PATHS.includes(pathname)) return true;
  return PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Public page — no auth required
  if (isPublic(pathname)) {
    return NextResponse.next();
  }

  const token = request.cookies.get("admin_token")?.value;
  if (token) {
    return NextResponse.next();
  }

  // No cookie → bounce to /login and remember where they were going
  const redirectUrl = request.nextUrl.clone();
  redirectUrl.pathname = "/login";
  if (pathname !== "/" && pathname !== "/login") {
    redirectUrl.searchParams.set("next", pathname);
  }
  return NextResponse.redirect(redirectUrl);
}

// Match everything except Next.js internals, API routes, and static assets.
// Public app routes (login, register, track) are filtered inside the middleware
// function itself so they get a clean "pass-through" code path.
export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
  ],
};
