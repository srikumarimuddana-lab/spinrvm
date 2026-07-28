# Change Impact & Risk Log — Data Transfer module: rate limit the export route

## Issue/gap identified
Flagged in the earlier critical review: `data_transfer_export.py` had no
rate limit at all, unlike the DSAR self-export (`@dsar_export_limit`,
3/hour). This route exports **unredacted** PII (profile, documents, ride
history, insurance periods) for up to 100 *other* users per call — a
compromised or malicious admin session could otherwise issue export after
export with no throttle, at a much larger blast radius than the DSAR
route's "one user exporting their own data."

## Root cause
The route was built without checking the existing rate-limiting convention
used by the analogous DSAR export it was explicitly modeled on elsewhere in
this module (ZIP-building shape, background-task pattern) — the rate limit
was the one piece of that precedent not carried over.

## Fix/remediation
- `backend/utils/rate_limiter.py`: added `data_transfer_export_limit =
  default_limiter.limit("10/hour")`, placed next to `dsar_export_limit` with
  a comment explaining the different threat model (admin exporting *other*
  users' data, not a user exporting their own). Same IP-keyed mechanism as
  every other limiter in this file (`default_limiter`'s `key_func` —
  `get_real_client_ip`, honoring `CF-Connecting-IP`/`X-Real-IP` over the
  spoofable `X-Forwarded-For`) — no new keying strategy invented; this
  codebase has no precedent for per-admin-id rate limiting, so introducing
  one here would be a bigger architectural change than this fix calls for.
- `backend/routes/admin/data_transfer_export.py`: applied
  `@data_transfer_export_limit` to `export_entities`, added the `request:
  Request = None` parameter SlowAPI's decorator introspects (same
  requirement documented on `dsar_export_limit`'s usage in
  `tax_exports.py`).

10/hour (vs. DSAR's 3/hour) reflects that a single admin export call can
legitimately need to run more than 3 times/hour during active onboarding
work across an environment migration, while still bounding total volume
far below "no limit at all."

## Risk & impact on existing functionality
Blast radius: `data_transfer_export_limit` is a new limiter constant with a
single consumer (`export_entities`) — grepped, no other route references it.
`default_limiter` itself is shared infrastructure used by every rate limit
in the codebase, but adding a new named `.limit(...)` call doesn't change
its behavior for any existing limiter (each `.limit()` call is independent
counter state, keyed by the limit string + client key). The decorator
ordering (`@router.post` outermost, `@data_transfer_export_limit`
innermost) exactly matches the established `dsar_export_limit` precedent,
so SlowAPI's request-detection mechanism behaves identically.

One real behavior change: an admin issuing an 11th export within an hour
now gets a 429 instead of the call proceeding. Given the earlier fix
already made this route backgrounded (not blocking), the practical impact
is "your export gets queued more slowly," not "the UI hangs" — a
tolerable tradeoff for the exfiltration-bounding this exists to provide.

## User experience effect
Visible only to an admin who exports more than 10 batches in an hour — a
429 response with SlowAPI's standard rate-limit error body. Normal usage
(a handful of exports during an onboarding/migration session) is
unaffected.

## Files modified
| File | What changed | Why |
|---|---|---|
| `backend/utils/rate_limiter.py` | +`data_transfer_export_limit = default_limiter.limit("10/hour")` | New named limiter, additive |
| `backend/routes/admin/data_transfer_export.py` | `@data_transfer_export_limit` decorator + `request: Request = None` param | Apply the limit to the export route |

## Before/after snippet
```python
# before
@router.post("/data-transfer/export", status_code=202)
async def export_entities(body: ExportRequest, background_tasks: BackgroundTasks, admin: dict = Depends(get_admin_user)):

# after
@router.post("/data-transfer/export", status_code=202)
@data_transfer_export_limit
async def export_entities(
    body: ExportRequest, background_tasks: BackgroundTasks, request: Request = None, admin: dict = Depends(get_admin_user)
):
```

## Rollback plan
Remove the `@data_transfer_export_limit` decorator and the `request`
parameter from `export_entities`, and the new constant from
`rate_limiter.py` (`git revert` is safe — no data or schema involved,
purely a request-time behavior change).

## Verification performed
- `python3 -m py_compile` on both modified files — passes.
- Confirmed the exact decorator order (`@router.post` then the rate-limit
  decorator) and `request: Request = None` parameter requirement by reading
  `dsar_export_limit`'s real usage in `tax_exports.py`, not assumed.
- Confirmed `default_limiter`'s key function
  (`get_real_client_ip`/`CF-Connecting-IP`/`X-Real-IP` precedence) is the
  same mechanism every other limiter in `rate_limiter.py` already uses —
  no new rate-limiting infrastructure introduced.
- Grepped for other consumers of `data_transfer_export_limit` — none;
  single, isolated addition.

## What was NOT verified
- `fastapi`/`slowapi` are not installed in this session's environment
  (consistent with every other backend verification note in this module's
  history) — could not actually trigger the decorator or observe a 429 at
  runtime; verified via `py_compile` and exact precedent-matching against
  `dsar_export_limit`'s working usage instead.
- Not exercised against a live Redis-backed rate limiter (or the in-memory
  fallback) to confirm the 10/hour threshold behaves as expected across
  concurrent requests.
