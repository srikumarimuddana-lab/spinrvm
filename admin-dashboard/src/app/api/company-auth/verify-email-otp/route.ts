import { randomBytes } from "crypto";
import { NextRequest, NextResponse } from "next/server";
import { BACKEND_URL, forwardedHeaders, setCompanySession } from "../_shared";

// PIPEDA data residency: pin to Canadian (Montreal) Vercel edge region.
export const preferredRegion = "yul1";

// Company-portal email OTP verification proxy. Same cookie contract as
// verify-otp: the backend returns refresh_token for non-browser clients, but
// this route strips it from the body and stores it in an HttpOnly cookie.
export async function POST(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "Invalid JSON" }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${BACKEND_URL}/api/portal/auth/verify-email-otp`, {
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

  if (!refresh_token) {
    return NextResponse.json(clientData, { status: 200 });
  }

  const csrf = randomBytes(32).toString("hex");
  const res = NextResponse.json({ ...clientData, csrf_token: csrf }, { status: 200 });
  setCompanySession(res, refresh_token, csrf, isProduction);
  return res;
}
