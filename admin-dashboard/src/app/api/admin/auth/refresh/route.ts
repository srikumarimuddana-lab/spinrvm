import { NextRequest, NextResponse } from "next/server";

// PIPEDA data residency: pin to Canadian (Toronto) region (F-22).
export const preferredRegion = "yyz1";

const BACKEND_URL =
  process.env.BACKEND_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://127.0.0.1:8000";

const RT_COOKIE = "spinr_admin_rt";
const CSRF_COOKIE = "csrf_token";
const RT_MAX_AGE = 30 * 24 * 60 * 60;

function clearRtCookie(res: NextResponse): void {
  res.cookies.set(RT_COOKIE, "", {
    httpOnly: true,
    sameSite: "strict",
    secure: process.env.NODE_ENV === "production",
    path: "/api/admin/auth",
    maxAge: 0,
  });
}

export async function POST(req: NextRequest) {
  // CSRF double-submit validation — cookie value must match the request header.
  // silentRefresh() bootstraps this by reading the csrf_token cookie from
  // document.cookie and echoing it as X-CSRF-Token before in-memory state is
  // available. Both must be present and equal.
  const csrfCookie = req.cookies.get(CSRF_COOKIE)?.value ?? null;
  const csrfHeader = req.headers.get("x-csrf-token") ?? null;
  if (!csrfCookie || !csrfHeader || csrfCookie !== csrfHeader) {
    return NextResponse.json({ detail: "CSRF validation failed" }, { status: 403 });
  }

  const refreshToken = req.cookies.get(RT_COOKIE)?.value;
  if (!refreshToken) {
    return NextResponse.json({ detail: "No refresh token" }, { status: 401 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${BACKEND_URL}/api/admin/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
  } catch {
    return NextResponse.json({ detail: "Backend unreachable" }, { status: 502 });
  }

  const data = await upstream.json();

  if (!upstream.ok) {
    const res = NextResponse.json(data, { status: upstream.status });
    // On a definitive 401 the token is revoked — clear the cookie.
    if (upstream.status === 401) clearRtCookie(res);
    return res;
  }

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
  // Rotate the browser-readable csrf_token cookie so the next page-reload
  // bootstrap picks up the fresh value returned by the backend.
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
