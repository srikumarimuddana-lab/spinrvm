# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code |
| Surface(s) | backend (paired change in `desktop_website`) |
| Domain (Sentry tag) | infra (App Check / rate limiting), ai (public assistant), auth (driver signup) |
| PR / commit link | branch `claude/dreamy-hopper-4y58dg` |
| Related issue or gap ID | none filed — production 401 on `POST /api/v1/ai/public-chat` (desktop-website Vercel logs, 2026-09-25); alternative to the App Check exemption in #5831 |

## 1. Issue / gap identified

The spinr.ca website calls this backend **from its own server** (Next.js route handlers and server components on Vercel — `desktop_website/lib/spinr-api.js`), not from visitors' browsers. With App Check enforced, every one of those calls that is not on `_APP_CHECK_EXEMPT_PREFIXES` gets `401 "App Check token required"`:

| Website call | Before this change |
|---|---|
| `POST /api/v1/ai/public-chat` | 401 on every message (confirmed in production logs) |
| `GET /api/v1/service-areas` | 401 (not exempt) — signup form cannot list areas |
| `GET /api/v1/legal-documents` | 401 (not exempt) — site silently falls back to its own copy |
| `POST /api/v1/drivers/register` | 401 (not exempt) — a web applicant verifies their phone, then fails at submit |

Separately, every call the website does make reaches the per-IP rate limiters keyed on **Vercel's egress IP**, so all website visitors share one bucket (e.g. 6/min for the whole site on public chat).

## 2. Root cause

App Check attests a registered mobile build. A Vercel function is not one and cannot mint a token, and there was no other way for a trusted Spinr server to identify itself. The per-IP limiter had no trustworthy source for the end visitor's IP on a server-to-server hop.

## 3. Fix / remediation

A signed server-to-server channel, `backend/core/web_caller.py`:

- The website signs each request: `HMAC-SHA256(secret, "v1\n{ts}\n{METHOD}\n{path}\n{query}\n{sha256(body)}\n{client_ip}")`, sent as `X-Spinr-Web-Timestamp`, `X-Spinr-Web-Client-IP`, `X-Spinr-Web-Signature: v1=<hex>`.
- `FirebaseAppCheckMiddleware` verifies it first. A valid signature, within ±300 s, on an **exact (method, path) allow-list** of the seven routes the website uses, skips App Check and stores the signed client IP on `request.state.web_client_ip`.
- `utils.rate_limiter.get_real_client_ip` prefers that verified IP, so website visitors get their own buckets. It is inside the HMAC, so it cannot be swapped to rotate buckets.
- `WEB_CALLER_SIGNING_SECRETS` (deploy secret, comma-separated for zero-downtime rotation, each ≥32 chars; the production settings guard refuses to start with a short entry, since it would otherwise be silently ignored).

The signature grants no identity: `/drivers/register` still requires the applicant's JWT and the assistant still runs at the anonymous `web` audience.

## 4. Risk & impact on existing functionality

- **Strictly additive.** A request without signature headers takes exactly the old path. A request whose signature fails verification also takes the old path (App Check then decides) and logs `Web caller: signature not accepted … (<reason>)`. No request that succeeded before can fail because of this change, and the website and backend can deploy in either order.
- **Secret unset = feature off** — identical behaviour to today.
- **Replay** within the 300 s window is possible for a captured request, unchanged. Every allow-listed route is safe to repeat (reads, an anonymous chat turn, an upsert behind a JWT); any change to method/path/query/body/IP breaks the signature.
- **Body read in middleware:** Starlette ≥0.28 caches the body for the route (pinned 1.3.1); covered by a test that the handler still reads the full body.
- **Rate-limit keying change** applies only to requests carrying a verified signature. For all other traffic `get_real_client_ip` is unchanged.
- **Interaction with #5831:** if #5831's exemption of `/api/v1/ai/public-chat` also merges, signed calls still get their per-visitor IP key (the signature check runs before the exempt list), but the endpoint is then also open to unsigned callers. Recommended: keep #5831's FAQ-retrieval changes and drop its App Check exemption in favour of this.

## 5. User-experience effect

- Website visitors: the chat widget reaches the backend assistant instead of falling back to canned FAQ matching; web driver applicants can finish submitting.
- Rider/driver apps: no change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/core/web_caller.py` | New: allow-list, canonical string, HMAC verify | The signed channel |
| `backend/core/middleware.py` | App Check middleware verifies a signature before its exempt/enforce logic | Let verified website calls through |
| `backend/utils/rate_limiter.py` | `get_real_client_ip` prefers the verified website client IP | Per-visitor rate limits for website traffic |
| `backend/core/config.py` | `WEB_CALLER_SIGNING_SECRETS` setting + production length guard | Configuration |
| `backend/tests/test_web_caller_signature.py` | New tests | See §8 |

## 7. Rollout

1. Generate a secret: `python -c 'import secrets; print(secrets.token_urlsafe(48))'`.
2. Set `WEB_CALLER_SIGNING_SECRETS` on the backend and `SPINR_WEB_SIGNING_SECRET` (same value) on the desktop-website Vercel project. Either order works.
3. Confirm: desktop-website `/api/agent/search` responses show `source: "spinr_backend"`; backend logs show no `Web caller: signature not accepted`.

Rollback: unset `WEB_CALLER_SIGNING_SECRETS` (no deploy of code needed) — behaviour returns to exactly today's.

## 8. Verification

`backend/tests/test_web_caller_signature.py`: valid signature passes enforced App Check and the handler still reads the body; signed client IP becomes the rate-limit key; signed GET with query; unsigned still 401; forged signature, swapped client IP, moved timestamp, tampered body, tampered query, wrong secret, stale timestamp, unlisted route, wrong method, unset secret — all get no bypass; rotation accepts either secret; short secrets never trusted; a golden vector pinned byte-for-byte against the website's JavaScript signer.

Not run locally: the authoring sandbox cannot reach PyPI, so the suite runs in CI. Lint/format (`ruff check`, `ruff format --check`) pass locally, and the golden vector was checked against the website's Node signer.
