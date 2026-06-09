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

// /mfa/confirm completes forced first-login enrollment: the backend now
// returns full session tokens alongside backup_codes. Like /login and
// /mfa/challenge, the refresh_token must be stripped from the JSON body and
// stored in the HttpOnly cookie so it never reaches browser JS, and the CSRF
// cookies must be installed so subsequent admin mutations pass the backend's
// double-submit check. The Authorization header (the enrollment-scoped token,
// or a session token for Settings-flow re-enrollment) is forwarded upstream.
export async function POST(req: NextRequest) {
  const body = await req.json();
  const auth = req.headers.get("authorization");

  let upstream: Response;
  try {
    upstream = await fetch(`${BACKEND_URL}/api/admin/auth/mfa/confirm`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(auth ? { Authorization: auth } : {}),
      },
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
  // backup_codes stay in clientData so the dialog can show them once.
  const { refresh_token, refresh_expires_at: _ignored, ...clientData } = data;

  const isProduction = process.env.NODE_ENV === "production";

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
    // Path "/" (not "/api/admin/auth"): authStore.initAuth() restores the
    // in-memory CSRF token by reading this cookie from document.cookie on
    // dashboard pages after a hard refresh / new tab. Path-scoping it away
    // from /dashboard would make the next /refresh omit X-CSRF-Token, 403,
    // and clear an otherwise-valid session.
    res.cookies.set(CSRF_COOKIE, csrfToken, {
      httpOnly: false,
      sameSite: "strict",
      secure: isProduction,
      path: "/",
      maxAge: SESSION_MAX_AGE,
    });
    // Backend CSRF middleware reads cookie name `csrf_token` (see
    // backend/core/middleware.py); without it admin PUT/POST/DELETE get 403.
    res.cookies.set("csrf_token", csrfToken, {
      httpOnly: false,
      sameSite: "strict",
      secure: isProduction,
      path: "/",
      maxAge: SESSION_MAX_AGE,
    });
    return res;
  }

  // No refresh token (Settings-flow re-enrollment by an already-logged-in
  // admin returns backup_codes only) — pass the body through unchanged.
  return NextResponse.json(clientData, { status: 200 });
}
