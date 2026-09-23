# Change Impact & Risk: load-test target origin guard

## Issue / root cause
The HTTP load harness accepted targets without a positive origin allowlist. It could follow HTTP/WebSocket redirects to another origin, and a saved token cache was not bound to the current target.

## Fix
Add one shared validator requiring explicit `LOADTEST_ALLOWED_ORIGINS` exact origins. Known canonical production API hosts are denied even if allowlisted, while documented staging subdomains can be explicitly allowed; non-loopback HTTP is rejected. Locust and pre-auth requests clients reject absolute cross-origin calls and disable redirects, and websocket-client is configured with zero redirects. Cached credentials must match the current allowed origin.

## Risk & impact
Load-test operators must set an exact comma-separated origin allowlist, such as `LOADTEST_ALLOWED_ORIGINS=https://spinr-backend-staging.fly.dev`. No application behavior or live data changes. The seed script's production Supabase project guard is reviewed separately.

## User experience
Load-test startup fails before requests on absent/malformed/mismatched configuration. Redirect responses surface as responses/errors instead of being followed.

## Files modified
| File | Change | Purpose |
|---|---|---|
| `loadtest/target_guard.py` | Exact-origin positive allowlist, production deny, redirect controls, token-cache binding | Keep traffic at the intended staging origin |
| `loadtest/test_target_guard.py` | Tests valid/invalid origins, production denial, cache binding, redirect options | Pin the guard behavior |
| This record | Risk and verification boundary | Required change impact record |

## Before / after
Before, the harness trusted a caller-supplied URL. After, the shared guard requires an explicit exact origin, rejects known production API origins, and prevents HTTP/WebSocket redirect following or explicit cross-origin HTTP calls.

## Rollback plan
Revert the isolated guard commit; it changes only local load-test behavior.

## Verification performed / not verified
- [x] `/tmp/pr5725-venv/bin/python -m pytest loadtest/test_target_guard.py -q` — 12 passed; `git diff --check` clean.
- [x] All checks use synthetic origins and fake clients; no network target was contacted.
- [ ] Entrypoint wiring, token-cache enforcement, seed database guard, and a real staging load run are separate follow-ups; no load run was executed.
