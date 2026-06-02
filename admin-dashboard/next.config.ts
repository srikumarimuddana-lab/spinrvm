import type { NextConfig } from "next";

// Fail fast on Vercel production if the API URL is missing — prevents silent
// localhost fallback. Uses VERCEL_ENV (not NODE_ENV) because next build always
// sets NODE_ENV=production even for local builds.
if (
  process.env.VERCEL_ENV === "production" &&
  !process.env.BACKEND_URL &&
  !process.env.NEXT_PUBLIC_API_URL
) {
  throw new Error(
    "NEXT_PUBLIC_API_URL must be set in production (admin-dashboard)"
  );
}

const BACKEND_URL =
  process.env.BACKEND_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://127.0.0.1:8000";

// Content-Security-Policy for the admin dashboard is set dynamically
// per-request in src/middleware.ts so a per-request nonce can be embedded.
// The public /track/* pages are excluded from middleware and get their own
// static CSP here — no nonce needed since they carry no privileged JS.
const TRACK_CSP = [
  "default-src 'self'",
  // MapLibre GL v5 needs blob: for its tile-processing Web Worker.
  // Chrome requires this even when the blob is created in the same origin.
  "worker-src blob: 'self'",
  // Next.js hydration scripts are inline; unsafe-inline is acceptable for
  // a public, low-privilege page that holds no admin state.
  "script-src 'self' 'unsafe-inline' https:",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: https:",
  // Glyphs and fonts fetched by MapLibre style may come from third-party CDNs.
  "font-src 'self' data: https:",
  // Tile and routing requests go to openfreemap.org, protomaps, osrm, etc.
  "connect-src 'self' https: wss: ws:",
  "frame-ancestors 'none'",
].join("; ");

const securityHeaders = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  // same-origin prevents driver_id/ride_id leaking in Referer to Stripe/Twilio ([23-5])
  { key: "Referrer-Policy", value: "same-origin" },
  { key: "X-Permitted-Cross-Domain-Policies", value: "none" },
  // Disable legacy IE/Edge XSS auditor — it had bypasses and is obsolete ([23-7])
  { key: "X-XSS-Protection", value: "0" },
  {
    key: "Strict-Transport-Security",
    value: "max-age=63072000; includeSubDomains; preload",
  },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
  },
];

const nextConfig: NextConfig = {
  allowedDevOrigins: ["192.168.68.63"],
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: securityHeaders,
      },
      // Public tracking page: static CSP with worker-src blob: so MapLibre's
      // tile-processing Web Worker is allowed. This overrides the generic
      // rule above for /track/* and is guaranteed to be applied by Vercel
      // regardless of middleware behaviour.
      {
        source: "/track/:path*",
        headers: [
          { key: "Content-Security-Policy", value: TRACK_CSP },
        ],
      },
    ];
  },
  async rewrites() {
    return [
      // Local API routes (must be before the catch-all /api rewrite).
      // Next.js file-system routes *should* take priority over rewrites,
      // but on Vercel the catch-all can shadow them depending on build
      // order and edge-function resolution. Explicit identity rewrites
      // guarantee the Next.js route handlers are always used.
      {
        source: "/api/auth/:path*",
        destination: "/api/auth/:path*",
      },
      // Admin auth routes (login, refresh, logout, mfa) are Next.js API
      // routes that manage HttpOnly cookies — they must NOT be proxied
      // to the backend or the RT cookie is never set / read.
      {
        source: "/api/admin/auth/:path*",
        destination: "/api/admin/auth/:path*",
      },
      // Proxy everything else to backend
      {
        source: "/api/:path*",
        destination: `${BACKEND_URL}/api/:path*`,
      },
      {
        source: "/ws/:path*",
        destination: `${BACKEND_URL}/ws/:path*`,
      },
    ];
  },
};

export default nextConfig;
