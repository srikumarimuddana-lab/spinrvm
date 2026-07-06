import { randomBytes } from "crypto";
import { NextRequest, NextResponse } from "next/server";

// PIPEDA data residency: pin to Canadian (Montreal) Vercel edge region.
export const preferredRegion = "yul1";

// Company-portal OTP verification proxy. Mirrors the admin login route: the
// backend's AuthResponse returns the 30-day refresh_token in the JSON body
// (for mobile clients that have no cookie jar), so a direct browser call
// would expose the long-lived credential to JS. This route proxies to the
// backend, strips refresh_token from the body, and holds it in an HttpOnly
// cookie (spinr_company_rt, scoped to /api/company-auth) instead. The
// readable csrf_token cookie is what the backend's double-submit middleware
// checks on the portal's direct /api/company/* writes.
const BACKEND_URL = (() => {
  const url =
    process.env.BACKEND_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    (process.env.NODE_ENV !== "production" ? "http://127.0.0.1:8000" : "");
  if (!url) {
    console.warn("BACKEND_URL env var is required in production.");
  }
  return url || "http://127.0.0.1:8000";
})();

const RT_COOKIE = "spinr_company_rt";
const SESSION_MAX_AGE = 30 * 24 * 60 * 60; // 30 days — matches backend RT TTL

export async function POST(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "Invalid JSON" }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${BACKEND_URL}/api/v1/auth/verify-otp`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    return NextResponse.json({ detail: "Backend unreachable" }, { status: 502 });
  }

  const data = await upstream.json().catch(() => ({}));
  if (!upstream.ok) {
    return NextResponse.json(data, { status: upstream.status });
  }

  const { refresh_token, refresh_expires_at: _ignored, ...clientData } = data;
  const isProduction = process.env.NODE_ENV === "production";

  // No refresh_token on a 200 (e.g. requires_reactivation) → no session; relay
  // the body verbatim with no cookies set.
  if (!refresh_token) {
    return NextResponse.json(clientData, { status: 200 });
  }

  // Self-issued CSRF token: value is echoed in the body (stored in the
  // company auth store) AND set as the csrf_token cookie, so the backend's
  // double-submit check (cookie === X-CSRF-Token header) passes on writes.
  const csrf = randomBytes(32).toString("hex");
  const res = NextResponse.json({ ...clientData, csrf_token: csrf }, { status: 200 });
  res.cookies.set(RT_COOKIE, refresh_token, {
    httpOnly: true,
    sameSite: "strict",
    secure: isProduction,
    path: "/api/company-auth",
    maxAge: SESSION_MAX_AGE,
  });
  res.cookies.set("csrf_token", csrf, {
    httpOnly: false,
    sameSite: "strict",
    secure: isProduction,
    path: "/",
    maxAge: SESSION_MAX_AGE,
  });
  return res;
}
