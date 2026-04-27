import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL =
  process.env.BACKEND_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://127.0.0.1:8000";

const RT_COOKIE = "spinr_admin_rt";
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
  return res;
}
