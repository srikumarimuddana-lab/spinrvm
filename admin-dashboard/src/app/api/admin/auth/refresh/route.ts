import { randomBytes, timingSafeEqual } from "crypto";
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

function clearSessionCookies(res: NextResponse): void {
  const isProduction = process.env.NODE_ENV === "production";
  res.cookies.set(RT_COOKIE, "", {
    httpOnly: true,
    sameSite: "strict",
    secure: isProduction,
    path: "/api/admin/auth",
    maxAge: 0,
  });
  res.cookies.set(CSRF_COOKIE, "", {
    httpOnly: false,
    sameSite: "strict",
    secure: isProduction,
    path: "/",
    maxAge: 0,
  });
  res.cookies.set("csrf_token", "", {
    httpOnly: false,
    sameSite: "strict",
    secure: isProduction,
    path: "/",
    maxAge: 0,
  });
}

function verifyCsrf(req: NextRequest): boolean {
  const cookieToken = req.cookies.get(CSRF_COOKIE)?.value;
  const headerToken = req.headers.get("x-csrf-token");
  if (!cookieToken || !headerToken) return false;
  try {
    const a = Buffer.from(cookieToken, "utf8");
    const b = Buffer.from(headerToken, "utf8");
    // timingSafeEqual requires equal-length buffers; mismatched length → reject.
    if (a.length !== b.length) return false;
    return timingSafeEqual(a, b);
  } catch {
    return false;
  }
}

export async function POST(req: NextRequest) {
  // Check RT cookie first — if absent there is no session to protect, so
  // return a clean 401 rather than running CSRF validation (which would
  // return 403 and destroy any existing cookies on a fresh page load).
  const refreshToken = req.cookies.get(RT_COOKIE)?.value;
  if (!refreshToken) {
    return NextResponse.json({ detail: "No refresh token" }, { status: 401 });
  }

  if (!verifyCsrf(req)) {
    const res = NextResponse.json({ detail: "CSRF validation failed" }, { status: 403 });
    clearSessionCookies(res);
    return res;
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
    if (upstream.status === 401) clearSessionCookies(res);
    return res;
  }

  const { refresh_token, refresh_expires_at: _ignored, ...clientData } = data;

  const isProduction = process.env.NODE_ENV === "production";

  // Rotate both cookies atomically — they must always be updated together
  // so the CSRF token always corresponds to the current HttpOnly RT cookie.
  // A missing refresh_token on a 200 is a backend contract violation; treat
  // it as a protocol error rather than silently issuing a mismatched CSRF token.
  if (refresh_token) {
    const newCsrfToken = randomBytes(32).toString("hex");
    const res = NextResponse.json(
      { ...clientData, csrf_token: newCsrfToken },
      { status: 200 },
    );
    res.cookies.set(RT_COOKIE, refresh_token, {
      httpOnly: true,
      sameSite: "strict",
      secure: isProduction,
      path: "/api/admin/auth",
      maxAge: SESSION_MAX_AGE,
    });
    res.cookies.set(CSRF_COOKIE, newCsrfToken, {
      httpOnly: false,
      sameSite: "strict",
      secure: isProduction,
      path: "/",
      maxAge: SESSION_MAX_AGE,
    });
    // Backend's CSRF middleware reads cookie name `csrf_token` — set it
    // alongside spinr_admin_csrf so the backend can validate proxied
    // requests. See login route for details.
    res.cookies.set("csrf_token", newCsrfToken, {
      httpOnly: false,
      sameSite: "strict",
      secure: isProduction,
      path: "/",
      maxAge: SESSION_MAX_AGE,
    });
    return res;
  }

  // Backend returned 200 without a refresh_token — protocol violation.
  return NextResponse.json({ detail: "Backend protocol error" }, { status: 502 });
}
