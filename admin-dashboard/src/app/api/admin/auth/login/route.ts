import { NextRequest, NextResponse } from "next/server";

// PIPEDA data residency: pin to Canadian (Montréal) region (F-22).
export const preferredRegion = "yul1";

const BACKEND_URL =
  process.env.BACKEND_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://127.0.0.1:8000";

const RT_COOKIE = "spinr_admin_rt";
const AT_COOKIE = "admin_token";
const CSRF_COOKIE = "csrf_token";
const RT_MAX_AGE = 30 * 24 * 60 * 60; // 30 days — matches backend refresh token TTL
const AT_MAX_AGE = 8 * 60 * 60;        // 8 hours — admin session cap (< 12h token TTL)

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

  const res = NextResponse.json(clientData, { status: 200 });
  if (refresh_token) {
    res.cookies.set(RT_COOKIE, refresh_token, {
      httpOnly: true,
      sameSite: "strict",
      secure: process.env.NODE_ENV === "production",
      path: "/api/admin/auth",
      maxAge: RT_MAX_AGE,
    });
  }
  // Set the access token as an HttpOnly cookie so Edge middleware can read it
  // without exposing it to XSS. JS code uses the in-memory Zustand copy for
  // Authorization headers — it never needs to read this cookie.
  if (clientData.token) {
    const atMaxAge = clientData.access_expires_at
      ? Math.max(0, Math.floor((new Date(clientData.access_expires_at).getTime() - Date.now()) / 1000))
      : AT_MAX_AGE;
    res.cookies.set(AT_COOKIE, clientData.token, {
      httpOnly: true,
      sameSite: "strict",
      secure: process.env.NODE_ENV === "production",
      path: "/",
      maxAge: atMaxAge,
    });
  }
  // Browser-readable CSRF cookie for the double-submit bootstrap on page reload.
  // Not HttpOnly so document.cookie can read it in silentRefresh().
  if (clientData.csrf_token) {
    res.cookies.set(CSRF_COOKIE, clientData.csrf_token, {
      httpOnly: false,
      sameSite: "strict",
      secure: process.env.NODE_ENV === "production",
      path: "/",
      maxAge: RT_MAX_AGE,
    });
  }
  return res;
}
