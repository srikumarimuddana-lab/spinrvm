import { timingSafeEqual } from "crypto";
import { NextRequest, NextResponse } from "next/server";
import logger from "@/lib/logger";

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

function verifyCsrf(req: NextRequest): boolean {
  const cookieToken = req.cookies.get(CSRF_COOKIE)?.value;
  const headerToken = req.headers.get("x-csrf-token");
  if (!cookieToken || !headerToken) return false;
  try {
    const a = Buffer.from(cookieToken, "utf8");
    const b = Buffer.from(headerToken, "utf8");
    if (a.length !== b.length) return false;
    return timingSafeEqual(a, b);
  } catch {
    return false;
  }
}

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
}

export async function POST(req: NextRequest) {
  if (!verifyCsrf(req)) {
    // Clear session cookies even on CSRF failure — consistent with refresh/route.ts
    // and prevents a live RT cookie persisting after a logout-race clears in-memory state.
    const res = NextResponse.json({ detail: "CSRF validation failed" }, { status: 403 });
    clearSessionCookies(res);
    return res;
  }

  const log = logger.child({ domain: "auth" });
  const refreshToken = req.cookies.get(RT_COOKIE)?.value;
  log.info({ has_rt: !!refreshToken }, "admin logout");

  // Fire revocation at the backend regardless of outcome — best-effort.
  if (refreshToken) {
    await fetch(`${BACKEND_URL}/api/admin/auth/logout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    }).catch((err) => {
      log.warn({ err }, "backend RT revocation failed (best-effort)");
    });
  }

  const res = NextResponse.json({ success: true });
  clearSessionCookies(res);
  return res;
}
