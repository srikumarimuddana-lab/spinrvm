import type { NextConfig } from "next";

const BACKEND_URL =
  process.env.BACKEND_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://127.0.0.1:8000";

// Content-Security-Policy is set dynamically per-request in src/proxy.ts
// (middleware) so a nonce can be embedded. Do NOT add a static CSP header here —
// a static header would override the nonce-bearing one set by middleware.
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
  // Admin uses none of these APIs; block them as defence-in-depth ([23-4])
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
    ];
  },
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

export default nextConfig;
