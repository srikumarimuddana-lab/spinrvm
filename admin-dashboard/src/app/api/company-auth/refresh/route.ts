import { randomBytes } from "crypto";
import { NextRequest, NextResponse } from "next/server";
import {
  BACKEND_URL,
  RT_COOKIE,
  forwardedHeaders,
  setCompanySession,
  clearCompanySession,
} from "../_shared";

export const preferredRegion = "yul1";

// Company-portal token refresh proxy. Runs server-side so the backend CSRF
// middleware (which only enforces on browser-Origin requests) is skipped —
// this is why refresh works even on a rehydrated page load where no CSRF token
// exists in JS memory. The refresh token lives only in the HttpOnly
// spinr_company_rt cookie (SameSite=strict → cross-site POSTs can't carry it,
// so no double-submit is needed here); the backend rotates it and we strip the
// rotated value from the body before returning to the browser.
export async function POST(req: NextRequest) {
  const isProduction = process.env.NODE_ENV === "production";
  const rt = req.cookies.get(RT_COOKIE)?.value;
  // No RT cookie → no session to restore. Clean 401 (not 403).
  if (!rt) {
    return NextResponse.json({ detail: "No refresh token" }, { status: 401 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${BACKEND_URL}/api/portal/auth/refresh`, {
      method: "POST",
      headers: forwardedHeaders(req),
      // Backend reads the refresh token from cookie OR body; we hold it in a
      // route-scoped cookie the backend fetch can't see, so pass it in the body.
      body: JSON.stringify({ refresh_token: rt }),
    });
  } catch {
    return NextResponse.json({ detail: "Backend unreachable" }, { status: 502 });
  }

  const data = await upstream.json().catch(() => ({}));
  if (!upstream.ok) {
    const res = NextResponse.json(data, { status: upstream.status });
    // A definitive 401 means the RT is revoked/rotated-away — clear cookies.
    if (upstream.status === 401) clearCompanySession(res, isProduction);
    return res;
  }

  const { refresh_token, refresh_expires_at: _ignored, ...clientData } = data;
  if (!refresh_token) {
    return NextResponse.json({ detail: "Backend protocol error" }, { status: 502 });
  }

  const csrf = randomBytes(32).toString("hex");
  const res = NextResponse.json({ ...clientData, csrf_token: csrf }, { status: 200 });
  setCompanySession(res, refresh_token, csrf, isProduction);
  return res;
}
