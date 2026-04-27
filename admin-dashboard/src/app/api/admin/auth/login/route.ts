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
    throw new Error(
      "BACKEND_URL env var is required in production. Set it in your Railway / Vercel environment.",
    );
  }
  return url;
})();

const RT_COOKIE = "spinr_admin_rt";
const CSRF_COOKIE = "spinr_admin_csrf";
// Both cookies live as long as the refresh token so they expire together.
const SESSION_MAX_AGE = 30 * 24 * 60 * 60; // 30 days — matches backend refresh token TTL

export async function POST(req: NextRequest) {
  const body = await req.json();

  let upstream: Response;
  try {
    upstream = await fetch(`${BACKEND_URL}/api/admin/auth/login`, {
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

  // Strip the refresh token from the response body — it must never reach
  // the browser's JS context. We store it in an HttpOnly cookie instead.
  const { refresh_token, refresh_expires_at: _ignored, ...clientData } = data;

  const isProduction = process.env.NODE_ENV === "production";

  // Only issue cookies when a real session was created (refresh_token present).
  // mfa_required responses carry no refresh token, so no cookies are set.
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
    // CSRF double-submit cookie: not HttpOnly so JS can read and send as
    // X-CSRF-Token header. SameSite=Strict means cross-origin requests
    // can't read it, so the header value proves same-origin intent.
    res.cookies.set(CSRF_COOKIE, csrfToken, {
      httpOnly: false,
      sameSite: "strict",
      secure: isProduction,
      path: "/api/admin/auth",
      maxAge: SESSION_MAX_AGE,
    });
    return res;
  }

  // mfa_required path — no session yet, return body only (no cookies).
  return NextResponse.json(clientData, { status: 200 });
}
