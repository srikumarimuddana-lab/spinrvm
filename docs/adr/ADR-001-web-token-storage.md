# ADR-001: Web Auth Token Storage — sessionStorage + Mitigations (short-term) / Edge BFF (long-term)

- **Date**: 2026-04-30
- **Status**: Accepted (short-term); Deferred (long-term target: Option 1)
- **Deciders**: Platform, Security
- **Ticket**: M-7

---

## Context

Spinr's rider-app is built on Expo SDK 54 and ships as:

1. **Native** (iOS/Android) — tokens stored in `SecureStore`. Not affected by this ADR.
2. **Web SPA** (`Platform.OS === 'web'`) — the current code path falls through to `localStorage` (see `rider-app/app/otp.tsx` and `shared/store/authStore.ts`). `localStorage` persists across browser sessions and is readable by any JavaScript on the page, making it the highest-risk storage option for auth tokens.

The ideal security posture for web tokens is **HttpOnly cookies**, which are opaque to JavaScript. However, HttpOnly cookies require a server runtime to set the `Set-Cookie` response header. The rider web surface is a **static SPA** deployed to Vercel with no server runtime — it cannot set HttpOnly cookies directly.

### Threat model context

- The rider web surface is **not the primary threat surface**; the vast majority of Spinr users are on native mobile apps.
- Expo Web limits third-party script exposure; the attack surface for XSS is materially lower than a typical marketing-heavy SPA.
- Access tokens already have a **15-minute TTL** (see `CLAUDE.md` token lifetimes), which bounds blast radius from token theft.
- Refresh tokens are **SHA-256 hashed at rest** and rotated on every use; a stolen refresh token from `localStorage` would be detectable and invalidatable.

---

## Decision

**Short-term (accepted now):** Move web storage from `localStorage` to **`sessionStorage`** and enforce the mitigations listed below. `sessionStorage` is cleared when the browser tab is closed, which eliminates the persistent-token-in-storage risk without requiring infrastructure changes.

**Long-term (deferred, target: next major web investment):** Introduce a **Vercel Edge Function BFF** (Option 1) that proxies the `/auth/*` endpoints, receives tokens from the backend, and sets them as HttpOnly SameSite=Strict cookies before forwarding the response to the browser. API calls from the SPA are proxied through the BFF so the cookie is always sent server-side.

This decision is **revisited** when any of the following conditions are met:

- Rider web MAU exceeds 10 % of total rider MAU
- A third-party SDK is added to the Expo Web bundle
- A security audit flags the SPA XSS surface as High or Critical
- The BFF effort is scoped and resourced within a sprint

---

## Mitigations required before this ADR is closed (short-term phase)

These controls **must be in place** for the `sessionStorage` decision to stand:

| Mitigation | Owner | Target |
|---|---|---|
| `Content-Security-Policy: script-src 'self'` on all Vercel web responses | Platform | Sprint M-7 |
| `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` headers | Platform | Sprint M-7 |
| `sessionStorage` replaces `localStorage` in `shared/store/authStore.ts` for `Platform.OS === 'web'` | Frontend | Sprint M-7 |
| Refresh token rotation on every use (already implemented; verify no regression) | Backend | Sprint M-7 |
| Confirm 15-min access token TTL is enforced in `backend/core/config.py` | Backend | Sprint M-7 |
| No third-party analytics or ad scripts added to Expo Web bundle without security review | All | Ongoing |

---

## Consequences

### Positive

- **No new infrastructure** required for the short-term fix — sessionStorage change is a two-file patch.
- **Reduced persistence risk** — sessionStorage tokens evaporate on tab close, unlike localStorage.
- **Honest trade-off** — the ADR documents the gap and the path to close it, rather than claiming false security.
- **15-min TTL already limits blast radius** — even if an XSS attack succeeds, the access token expires quickly and the refresh token rotation means stolen tokens are detectable.

### Negative

- **sessionStorage is still JavaScript-accessible** — an XSS attack in the same origin can still read it. This is not equivalent to HttpOnly cookie protection.
- **Tab-per-session UX** — users opening a second tab will need to re-authenticate. Acceptable for a web surface that is not the primary product.
- **Technical debt** — the long-term BFF approach adds complexity we are explicitly deferring.

### Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| XSS exfiltrates sessionStorage token | Low (strict CSP, limited third-party scripts) | Medium (15-min window) | CSP header, no third-party scripts, TTL |
| Refresh token stolen from sessionStorage | Low | High (account takeover window until rotation detected) | Rotation on use; manual revocation endpoint |
| BFF never gets resourced, debt compounds | Medium | Medium | ADR review triggers defined above |

---

## Alternatives considered

### Option 1 — Vercel Edge Function BFF (deferred long-term target)

A thin Next.js or Vercel Edge runtime proxies `/auth/*` requests, sets HttpOnly SameSite=Strict cookies, and forwards API calls so the browser never touches raw tokens. This is the correct end-state. Deferred because it requires non-trivial infrastructure work (new runtime, CORS changes, cookie domain alignment across `spinrapp.ca` subdomains) and the rider web surface does not yet warrant that investment.

### Option 2 — sessionStorage + mitigations *(this decision)*

See above.

### Option 3 — Switch Expo Web to a Next.js shell

Long-term architectural option to migrate the web surface to the `admin-dashboard` Next.js app or a new Next.js project, which has a proper server runtime. Very high effort, multi-quarter, outside the scope of M-7. Noted here for future architectural planning.

### Option 4 — Service Worker proxy

A service worker intercepts all API calls, injects tokens from an in-memory store seeded by a Secure same-site cookie. Moderate effort, but fragile on PWA update lifecycle (stale service worker can hold stale tokens), complex to debug, and the security improvement over sessionStorage + CSP is marginal given the threat model. Rejected.

---

## References

- `rider-app/app/otp.tsx` — current web auth flow
- `shared/store/authStore.ts` — platform-conditional storage logic
- `backend/core/config.py` — token TTL settings
- `CLAUDE.md` — token lifetimes, PIPEDA constraints, JWT trust model
- OWASP: [HTML5 Security Cheat Sheet — Storage](https://cheatsheetseries.owasp.org/cheatsheets/HTML5_Security_Cheat_Sheet.html)
