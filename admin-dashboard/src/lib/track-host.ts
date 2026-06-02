// Shared helpers for the public ride-tracking surface.
//
// The admin dashboard and the customer tracking page are the same Next.js
// app deployed once, but served on two domains:
//   - admin-spinr.spinr.ca  → full admin panel (login, dashboard, …)
//   - track.spinr.ca        → ONLY the read-only /track page, clean URLs
//                             (track.spinr.ca/{token}), admin routes blocked.
//
// Host-based routing in src/middleware.ts uses isTrackingHost() to decide
// which behaviour applies. AuthInitializer uses it to avoid bootstrapping an
// admin session on the public domain (which would just 401 on every load).

// Comma-separated list of hostnames that serve the public tracking page.
// NEXT_PUBLIC_ so it is inlined into the client bundle as well as the edge
// middleware. Defaults to the production tracking subdomain.
const TRACKING_HOSTS_RAW = process.env.NEXT_PUBLIC_TRACKING_HOSTS ?? "track.spinr.ca";

export function trackingHosts(): string[] {
  return TRACKING_HOSTS_RAW.split(",")
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
}

/** True when the given Host header / hostname serves the public tracking page. */
export function isTrackingHost(host: string | null | undefined): boolean {
  if (!host) return false;
  const hostname = host.split(":")[0].toLowerCase(); // strip :port if present
  return trackingHosts().includes(hostname);
}

/**
 * Content-Security-Policy for the public tracking page. No per-request nonce
 * (the page holds no admin state); Google Maps + OSRM need broad https sources.
 * Kept here so middleware (rewrite responses) and next.config.ts (direct
 * /track/* requests) emit an identical policy.
 */
export const TRACK_CSP = [
  "default-src 'self'",
  // Google Maps JS API injects its loader + tile scripts from googleapis hosts.
  "script-src 'self' 'unsafe-inline' https:",
  "style-src 'self' 'unsafe-inline' https:",
  "img-src 'self' data: blob: https:",
  "font-src 'self' data: https:",
  // Backend status poll, OSRM routing, Google Maps tiles/imagery.
  "connect-src 'self' https: wss: ws:",
  // Google Maps uses a same-origin blob: worker for tile rendering.
  "worker-src 'self' blob:",
  "frame-ancestors 'none'",
].join("; ");
