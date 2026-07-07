import { NextRequest, NextResponse } from "next/server";
import { BACKEND_URL, RT_COOKIE, forwardedHeaders, clearCompanySession } from "../_shared";

export const preferredRegion = "yul1";

// Company-portal logout proxy: revoke the rider refresh token server-side so a
// logged-out shared desk machine can't silent-refresh a new session, then clear
// the portal's auth cookies. Best-effort — a failed backend revoke must not
// block the local sign-out.
export async function POST(req: NextRequest) {
  const isProduction = process.env.NODE_ENV === "production";
  const rt = req.cookies.get(RT_COOKIE)?.value;
  const auth = req.headers.get("authorization");

  if (rt) {
    try {
      // Backend /auth/logout requires the bearer (get_current_user) and
      // revokes the refresh token from the body.
      await fetch(`${BACKEND_URL}/api/portal/auth/logout`, {
        method: "POST",
        headers: {
          ...forwardedHeaders(req),
          ...(auth ? { Authorization: auth } : {}),
        },
        body: JSON.stringify({ refresh_token: rt }),
      });
    } catch {
      /* best-effort revoke */
    }
  }

  const res = NextResponse.json({ ok: true });
  clearCompanySession(res, isProduction);
  return res;
}
