import { NextRequest, NextResponse } from "next/server";

// PIPEDA data residency: pin to Canadian (Toronto) region (F-22).
export const preferredRegion = "yyz1";

const BACKEND_URL =
  process.env.BACKEND_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://127.0.0.1:8000";

const RT_COOKIE = "spinr_admin_rt";
const RT_MAX_AGE = 30 * 24 * 60 * 60; // 30 days — matches backend refresh token TTL

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
  return res;
}
