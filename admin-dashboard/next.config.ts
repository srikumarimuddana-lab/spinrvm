import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";
import { TRACK_CSP } from "./src/lib/track-host";

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
// The public /track/* pages get the static TRACK_CSP (shared with middleware
// via src/lib/track-host.ts) — no nonce needed since they carry no admin state.

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
      // Public tracking page: static CSP (Google Maps + OSRM sources). Applies
      // to direct /track/* requests on any host; the tracking-domain rewrite
      // (/{token} → /track/{token}) sets the same CSP from middleware.
      {
        source: "/track/:path*",
        headers: [
          { key: "Content-Security-Policy", value: TRACK_CSP },
        ],
      },
    ];
  },
  async redirects() {
    return [
      // Records & Compliance consolidation: Data Transfer, Compliance,
      // Bulk Operations, and Export Approvals used to be four separate
      // sidebar entries; they're now tabs on one page. Redirect (not
      // remove) the old routes so nothing bookmarked or linked from an
      // old audit log/support ticket 404s — each old route's content is
      // still the exact same component, just reached via a tab param now.
      { source: "/dashboard/data-transfer", destination: "/dashboard/records?tab=data-transfer", permanent: false },
      { source: "/dashboard/compliance", destination: "/dashboard/records?tab=compliance", permanent: false },
      { source: "/dashboard/bulk-operations", destination: "/dashboard/records?tab=bulk-operations", permanent: false },
      { source: "/dashboard/export-approvals", destination: "/dashboard/records?tab=export-approvals", permanent: false },
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
      // Company-portal auth routes (set-cookie, verify-otp, refresh, logout)
      // are Next.js API routes that manage HttpOnly cookies (spinr_company_rt)
      // and strip the refresh_token from proxied bodies — they must NOT be
      // proxied to the backend or company login/refresh silently breaks.
      {
        source: "/api/company-auth/:path*",
        destination: "/api/company-auth/:path*",
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

// Sentry build integration. Required by @sentry/nextjs v9+ to wire the
// instrumentation hooks into the build; without it src/instrumentation*.ts are
// not registered and no events are captured.
//
// Source-map upload is OPTIONAL here and is skipped when SENTRY_AUTH_TOKEN is
// absent from the build environment — the build still succeeds, stack traces
// are just minified. Set SENTRY_AUTH_TOKEN + SENTRY_ORG + SENTRY_PROJECT in
// Vercel to get readable frames. These are BUILD-time vars and are unrelated to
// the runtime NEXT_PUBLIC_SENTRY_DSN / SENTRY_DSN.
//
// tunnelRoute is deliberately NOT enabled: it mounts a proxy route on the admin
// domain, which src/middleware.ts would subject to admin auth and the IP
// allowlist. The CSP already permits direct ingestion (connect-src 'self' https:).
export default withSentryConfig(nextConfig, {
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  // Keep build logs quiet locally, verbose in CI where the output is the record.
  silent: !process.env.CI,
  widenClientFileUpload: true,
  telemetry: false,
});
