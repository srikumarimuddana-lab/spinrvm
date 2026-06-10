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

// next.config.ts identity-rewrites /api/admin/auth/:path* to local Next.js
// routes (so the cookie-managing handlers always win over the backend
// catch-all proxy). Side effect: any admin-auth path WITHOUT a local handler
// 404s instead of reaching FastAPI. /mfa/enroll manages no cookies, so this
// is a plain pass-through proxy forwarding the Authorization header (a
// session token from Settings, or the enrollment-scoped token from the
// forced first-login flow).
export async function POST(req: NextRequest) {
  const auth = req.headers.get("authorization");

  let upstream: Response;
  try {
    upstream = await fetch(`${BACKEND_URL}/api/admin/auth/mfa/enroll`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(auth ? { Authorization: auth } : {}),
      },
    });
  } catch {
    return NextResponse.json({ detail: "Backend unreachable" }, { status: 502 });
  }

  const data = await upstream.json();
  return NextResponse.json(data, { status: upstream.status });
}
