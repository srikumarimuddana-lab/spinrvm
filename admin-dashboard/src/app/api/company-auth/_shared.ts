import { NextRequest, NextResponse } from "next/server";

// Shared helpers for the company-portal auth proxy routes (verify-otp,
// refresh, logout). Not a route file (underscore prefix) → Next never treats
// it as an endpoint.

export const BACKEND_URL = (() => {
  const url =
    process.env.BACKEND_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    (process.env.NODE_ENV !== "production" ? "http://127.0.0.1:8000" : "");
  if (!url) console.warn("BACKEND_URL env var is required in production.");
  return url || "http://127.0.0.1:8000";
})();

export const RT_COOKIE = "spinr_company_rt";
// Per-audience CSRF cookie: the staff admin surface and the company portal are
// the same browser origin, so a shared `csrf_token` cookie collides when both
// sessions are active. The company portal uses its own name; the backend CSRF
// middleware validates the X-CSRF-Token header against either cookie.
export const CSRF_COOKIE = "csrf_token_company";
// Middleware-visible presence marker: the access token (company_token) expires
// in 15 min (rider TTL), but the refresh session lasts 30 days. This flag lets
// the middleware load the /company-portal shell after access expiry so the
// client can silentRefresh, instead of hard-redirecting to /company-login.
export const SESSION_MARKER = "company_session";
export const SESSION_MAX_AGE = 30 * 24 * 60 * 60; // 30 days — matches backend RT

/**
 * Forward the real client IP + user-agent to the backend. Without this the
 * backend rate limiters (get_ipaddr → X-Forwarded-For) key every proxied OTP
 * verify / refresh to the Vercel/Railway egress address, so one busy portal
 * exhausts the shared bucket and 429s unrelated company users.
 */
export function forwardedHeaders(req: NextRequest): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  const xff = req.headers.get("x-forwarded-for");
  const xri = req.headers.get("x-real-ip");
  const ua = req.headers.get("user-agent");
  if (xff) h["x-forwarded-for"] = xff;
  if (xri) h["x-real-ip"] = xri;
  if (ua) h["user-agent"] = ua;
  return h;
}

export function setCompanySession(
  res: NextResponse,
  refreshToken: string,
  csrf: string,
  isProduction: boolean
): void {
  res.cookies.set(RT_COOKIE, refreshToken, {
    httpOnly: true,
    sameSite: "strict",
    secure: isProduction,
    path: "/api/company-auth",
    maxAge: SESSION_MAX_AGE,
  });
  // Company CSRF cookie for the backend double-submit on direct /api/company/*
  // writes (value echoed to the store as csrfToken). Distinct name so it never
  // collides with the staff admin's csrf_token cookie on the shared origin.
  res.cookies.set(CSRF_COOKIE, csrf, {
    httpOnly: false,
    sameSite: "strict",
    secure: isProduction,
    path: "/",
    maxAge: SESSION_MAX_AGE,
  });
  // Non-sensitive presence marker for the middleware (lax so it survives a
  // top-level navigation/reload into /company-portal).
  res.cookies.set(SESSION_MARKER, "1", {
    httpOnly: true,
    sameSite: "lax",
    secure: isProduction,
    path: "/",
    maxAge: SESSION_MAX_AGE,
  });
}

export function clearCompanySession(res: NextResponse, isProduction: boolean): void {
  res.cookies.set(RT_COOKIE, "", {
    httpOnly: true,
    sameSite: "strict",
    secure: isProduction,
    path: "/api/company-auth",
    maxAge: 0,
  });
  res.cookies.set(CSRF_COOKIE, "", {
    httpOnly: false,
    sameSite: "strict",
    secure: isProduction,
    path: "/",
    maxAge: 0,
  });
  res.cookies.set(SESSION_MARKER, "", {
    httpOnly: true,
    sameSite: "lax",
    secure: isProduction,
    path: "/",
    maxAge: 0,
  });
}
