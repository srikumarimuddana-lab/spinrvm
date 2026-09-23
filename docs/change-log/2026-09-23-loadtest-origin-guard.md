# Change Impact & Risk: load-test target origin guard

## Issue / root cause
The HTTP load harness accepted targets without a positive origin allowlist. It could follow HTTP/WebSocket redirects to another origin, and a saved token cache was not bound to the current target.

## Fix
Add one shared validator requiring explicit `LOADTEST_ALLOWED_ORIGINS` exact origins. Known canonical production API hosts are denied even if allowlisted, while documented staging subdomains can be explicitly allowed; non-loopback HTTP is rejected. Locust validates its host and cached token-cache origin before login or cache consumption, rejects absolute cross-origin calls and disables redirects, and configures websocket-client with zero redirects. Pre-auth requests use the same cross-origin and redirect controls. The seed script independently requires `EXPECTED_SUPABASE_PROJECT_REF` to match the nonproduction Supabase URL before DB initialization (implemented in the separate reviewed seed-target guard commit).

## Risk & impact
Load-test operators must set an exact comma-separated origin allowlist, such as `LOADTEST_ALLOWED_ORIGINS=https://staging-api.spinr.ca`, for both pre-auth and Locust. Cached tokens are rejected if their recorded `base_url` differs from the current validated origin. The seeder additionally requires `EXPECTED_SUPABASE_PROJECT_REF` set independently to the intended nonproduction project's 20-character ref. No application behavior or live data changes.

## User experience
Load-test startup fails before requests on absent/malformed/mismatched configuration. Redirect responses surface as responses/errors instead of being followed.

## Files modified
| File | Change | Purpose |
|---|---|---|
| `loadtest/target_guard.py` | Exact-origin positive allowlist, production deny, redirect controls, token-cache binding | Keep traffic at the intended staging origin |
| `loadtest/test_target_guard.py` | Tests valid/invalid origins, production denial, cache binding, redirect options | Pin the guard behavior |
| `loadtest/locustfile.py` | Validate target/cache before login and guard HTTP/WebSocket requests | Enforce target constraints in the timed harness |
| This record | Risk and verification boundary | Required change impact record |

## Before / after
Before, the harness trusted a caller-supplied URL. After, the shared guard requires an explicit exact origin, rejects known production API origins, and prevents HTTP/WebSocket redirect following or explicit cross-origin HTTP calls; the cached credential origin must match the current host before use.

## Rollback plan
Revert the isolated guard commit; it changes only local load-test behavior.

## Verification performed / not verified
- [x] `/tmp/pr5725-venv/bin/python -m pytest loadtest/test_target_guard.py loadtest/test_preauth_target_guard.py -q` — 17 passed; `git diff --check` clean.
- [x] All checks use synthetic origins and fake clients; no network target was contacted.
- [x] Locust entrypoints validate before login, wire HTTP/WS redirect controls, and validate cache origin before `_login` consumes cached credentials.
- [ ] A real staging load run was not executed.
