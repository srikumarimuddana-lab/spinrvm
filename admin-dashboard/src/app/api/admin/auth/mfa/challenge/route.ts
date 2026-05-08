import { randomBytes } from "crypto";
import { NextRequest, NextResponse } from "next/server";

// PIPEDA data residency: pin to Canadian (Montreal) Vercel edge region (F-22).
export const preferredRegion = "yul1";

const BACKEND_URL = (() => {
  const url =
    process.env.BACKEND_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    (process.env.NODE_ENV !== "production" ? "http://127.0.0.1:8000" : "");
  if (!url) {
    console.warn(
      "BACKEND_URL env var is required in production. Set it in your Railway / Vercel environment.",
    );
  }
  return url || "http://127.0.0.1:8000";
})();

const RT_COOKIE = "spinr_admin_rt";
const CSRF_COOKIE = "spinr_admin_csrf";
const SESSION_MAX_AGE = 30 * 24 * 60 * 60;

export async function POST(req: NextRequest) {
  const body = await req.json();

  let upstream: Response;
  try {
    upstream = await fetch(`${BACKEND_URL}/api/admin/auth/mfa/challenge`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    return NextResponse.json({ detail: "Backend unreachable" }, { status: 502 });
  }

  const data = await upstream.json();

  if (!upstream.ok) {
    return NextResponse.json(data, { status: upstream.status });
  }

  // Strip the refresh token — must never reach the browser's JS context.
  const { refresh_token, refresh_expires_at: _ignored, ...clientData } = data;

  const isProduction = process.env.NODE_ENV === "production";

  // Only issue cookies when a real session was created (refresh_token present).
  // If the backend returns 200 without a refresh_token the protocol contract
  // is violated — return the response without a CSRF token so the client
  // cannot receive a csrf_token value that has no matching HttpOnly cookie.
  if (refresh_token) {
    const csrfToken = randomBytes(32).toString("hex");
    const res = NextResponse.json(
      { ...clientData, csrf_token: csrfToken },
      { status: 200 },
    );
    res.cookies.set(RT_COOKIE, refresh_token, {
      httpOnly: true,
      sameSite: "strict",
      secure: isProduction,
      path: "/api/admin/auth",
      maxAge: SESSION_MAX_AGE,
    });
    res.cookies.set(CSRF_COOKIE, csrfToken, {
      httpOnly: false,
      sameSite: "strict",
      secure: isProduction,
      path: "/api/admin/auth",
      maxAge: SESSION_MAX_AGE,
    });
    return res;
  }

  return NextResponse.json(clientData, { status: 200 });
}
