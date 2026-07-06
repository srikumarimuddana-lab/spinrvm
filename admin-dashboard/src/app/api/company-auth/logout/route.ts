import { NextRequest, NextResponse } from "next/server";

export const preferredRegion = "yul1";

// Company-portal logout proxy: revoke the rider refresh token server-side so a
// logged-out shared desk machine can't silent-refresh a new session, then
// clear the portal's auth cookies. Best-effort — a failed backend revoke must
// not block the local sign-out.
const BACKEND_URL = (() => {
  const url =
    process.env.BACKEND_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    (process.env.NODE_ENV !== "production" ? "http://127.0.0.1:8000" : "");
  return url || "http://127.0.0.1:8000";
})();

const RT_COOKIE = "spinr_company_rt";

export async function POST(req: NextRequest) {
  const isProduction = process.env.NODE_ENV === "production";
  const rt = req.cookies.get(RT_COOKIE)?.value;
  const auth = req.headers.get("authorization");

  if (rt) {
    try {
      // Backend /auth/logout requires the bearer (get_current_user) and
      // revokes the refresh token from the body.
      await fetch(`${BACKEND_URL}/api/v1/auth/logout`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(auth ? { Authorization: auth } : {}),
        },
        body: JSON.stringify({ refresh_token: rt }),
      });
    } catch {
      /* best-effort revoke */
    }
  }

  const res = NextResponse.json({ ok: true });
  res.cookies.set(RT_COOKIE, "", {
    httpOnly: true,
    sameSite: "strict",
    secure: isProduction,
    path: "/api/company-auth",
    maxAge: 0,
  });
  res.cookies.set("csrf_token", "", {
    httpOnly: false,
    sameSite: "strict",
    secure: isProduction,
    path: "/",
    maxAge: 0,
  });
  return res;
}
