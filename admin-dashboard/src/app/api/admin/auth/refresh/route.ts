import { randomUUID } from "crypto";
import { NextRequest, NextResponse } from "next/server";
import logger from "@/lib/logger";

// PIPEDA data residency: pin to Canadian (Montréal) region (F-22).
export const preferredRegion = "yul1";

const BACKEND_URL =
  process.env.BACKEND_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://127.0.0.1:8000";

const RT_COOKIE = "spinr_admin_rt";
const AT_COOKIE = "admin_token";
const CSRF_COOKIE = "csrf_token";
const RT_MAX_AGE = 30 * 24 * 60 * 60;
const AT_MAX_AGE = 8 * 60 * 60;

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
  const request_id = randomUUID();
  const log = logger.child({ request_id, domain: "auth" });

  // CSRF double-submit validation — cookie value must match the request header.
  // silentRefresh() bootstraps this by reading the csrf_token cookie from
  // document.cookie and echoing it as X-CSRF-Token before in-memory state is
  // available. Both must be present and equal.
  const csrfCookie = req.cookies.get(CSRF_COOKIE)?.value ?? null;
  const csrfHeader = req.headers.get("x-csrf-token") ?? null;
  if (!csrfCookie || !csrfHeader || csrfCookie !== csrfHeader) {
    log.warn({ reason: "csrf_mismatch" }, "token refresh rejected: CSRF validation failed");
    return NextResponse.json({ detail: "CSRF validation failed" }, { status: 403 });
  }

  const refreshToken = req.cookies.get(RT_COOKIE)?.value;
  if (!refreshToken) {
    log.warn({ reason: "no_rt_cookie" }, "token refresh rejected: no RT cookie");
    return NextResponse.json({ detail: "No refresh token" }, { status: 401 });
  }

  let upstream: Response;
  const start = Date.now();
  try {
    upstream = await fetch(`${BACKEND_URL}/api/admin/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
  } catch (err) {
    log.error({ err, duration_ms: Date.now() - start }, "backend unreachable on token refresh");
    return NextResponse.json({ detail: "Backend unreachable" }, { status: 502 });
  }

  const duration_ms = Date.now() - start;
  const data = await upstream.json();

  if (!upstream.ok) {
    log.warn({ status: upstream.status, duration_ms }, "token refresh rejected by backend");
    const res = NextResponse.json(data, { status: upstream.status });
    // On a definitive 401 the token is revoked — clear the cookie.
    if (upstream.status === 401) clearRtCookie(res);
    return res;
  }

  log.info({ duration_ms }, "admin token refreshed");

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
  // Rotate the HttpOnly access-token cookie alongside the refresh token.
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
