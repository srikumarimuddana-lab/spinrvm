import { NextRequest, NextResponse } from "next/server";

// PIPEDA data residency: pin to Canadian (Toronto) region (F-22).
export const preferredRegion = "yyz1";

const BACKEND_URL =
  process.env.BACKEND_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://127.0.0.1:8000";

const RT_COOKIE = "spinr_admin_rt";

export async function POST(req: NextRequest) {
  const refreshToken = req.cookies.get(RT_COOKIE)?.value;

  // Fire revocation at the backend regardless of outcome — best-effort.
  if (refreshToken) {
    await fetch(`${BACKEND_URL}/api/admin/auth/logout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    }).catch(() => {});
  }

  const res = NextResponse.json({ success: true });
  res.cookies.set(RT_COOKIE, "", {
    httpOnly: true,
    sameSite: "strict",
    secure: process.env.NODE_ENV === "production",
    path: "/api/admin/auth",
    maxAge: 0,
  });
  return res;
}
