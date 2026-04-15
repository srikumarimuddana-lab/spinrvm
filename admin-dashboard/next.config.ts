import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

// Backend origin used by the dev/prod rewrite proxy.
// - Local dev: falls back to http://127.0.0.1:8000
// - Vercel: set BACKEND_URL (preferred) or NEXT_PUBLIC_API_URL in the
//   project env vars to your Railway backend, e.g.
//   https://spinr-backend-production.up.railway.app
//
// Note: Next.js rewrites only accept http:// or https:// destinations,
// so we use the same origin for both /api/* and /ws/*. The ws upgrade
// still works over that origin because WebSockets ride on http(s).
const BACKEND_URL =
  process.env.BACKEND_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
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

// Wrap with Sentry only when DSN is configured — avoids build warnings in
// local dev and CI runs that don't set SENTRY_DSN.
export default process.env.NEXT_PUBLIC_SENTRY_DSN || process.env.SENTRY_DSN
  ? withSentryConfig(nextConfig, {
      // Suppresses Sentry CLI output during builds.
      silent: !process.env.CI,
      // Automatically tree-shake Sentry debug code in production.
      disableLogger: true,
      // Upload source maps to Sentry for readable stack traces.
      // Requires SENTRY_AUTH_TOKEN in CI env.
      widenClientFileUpload: true,
    })
  : nextConfig;
