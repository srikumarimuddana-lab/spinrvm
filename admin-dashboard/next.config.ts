import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8001/api/:path*",
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
