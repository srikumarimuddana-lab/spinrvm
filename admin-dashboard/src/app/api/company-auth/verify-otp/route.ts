import { randomBytes } from "crypto";
import { NextRequest, NextResponse } from "next/server";
import { BACKEND_URL, forwardedHeaders, setCompanySession } from "../_shared";

// PIPEDA data residency: pin to Canadian (Montreal) Vercel edge region.
export const preferredRegion = "yul1";

// Company-portal OTP verification proxy. The backend's AuthResponse returns the
// 30-day refresh_token in the JSON body (for mobile clients that have no cookie
// jar), so a direct browser call would expose the long-lived credential to JS.
// This route strips refresh_token from the body and holds it in an HttpOnly
// spinr_company_rt cookie (scoped to /api/company-auth) instead — mirroring the
// admin login route. Client IP/UA are forwarded so the backend OTP rate limiter
// keys on the browser, not the egress address.
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
      headers: forwardedHeaders(req),
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

  // Self-issued CSRF token: echoed in the body (stored in the company auth
  // store) AND set as the csrf_token cookie, so the backend double-submit
  // (cookie === X-CSRF-Token header) passes on writes.
  const csrf = randomBytes(32).toString("hex");
  const res = NextResponse.json({ ...clientData, csrf_token: csrf }, { status: 200 });
  setCompanySession(res, refresh_token, csrf, isProduction);
  return res;
}
