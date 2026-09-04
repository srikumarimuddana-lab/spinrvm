# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude Code (session `01Hfvg3vjxhXapC25CK7DFZs`) |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | branch `claude/ride-offer-ringtone-edges-wl1w4c` |
| Related issue or gap ID | Same user report as `2026-09-04-ride-offer-ringtone-resume-dedup.md`: iOS killed/backgrounded ride-offer alert plays once, can't loop, and can't bypass silent mode/DND — a platform ceiling, not a bug, without Apple's Critical Alerts entitlement |

## 1. Issue / gap identified

`backend/features.py` and `backend/utils/push_retry.py` both send the iOS ride-offer APNs push with a plain-string `sound="ride_offer.caf"`. A standard APNs sound plays exactly once and always respects the device's silent switch/Do Not Disturb — Apple has no OS-level mechanism to loop or force-bypass those for a standard alert. The only sanctioned way to change that is a Critical Alert, which Apple gates behind a per-app entitlement (`com.apple.developer.usernotifications.critical-alerts`) that this app does not currently hold.

## 2. Root cause

Not a code bug — a missing capability. Getting a louder, DND-bypassing, loopable iOS alert requires Apple's approval (an entitlement request, outside this repo) **and** a new driver-app build with that entitlement compiled in, adopted by drivers. Neither exists yet.

## 3. Fix / remediation

This change adds **dormant scaffolding only** — no behavior change today:
- `backend/core/config.py`: new `Settings.IOS_CRITICAL_ALERTS_ENABLED: bool = False` field, with a comment stating exactly what must be true (entitlement granted + shipped build) before it's safe to flip.
- `backend/features.py` (`_deliver_push_now`) and `backend/utils/push_retry.py` (the matching retry-path push builder): the iOS dispatch `aps.sound` field now branches on that setting — `messaging.CriticalSound(name="ride_offer.caf", critical=True, volume=1.0)` when `True`, unchanged `"ride_offer.caf"` string when `False` (the default, and the only value that has ever shipped).

Deliberately **out of scope** for this change: `driver-app/services/notifeeService.ts`'s client-side `ios: { critical: false }` (used only by the narrower foreground/backgrounded-but-connected local re-display path, not the killed-app APNs path this fix targets) is left untouched — wiring it to the same flag would require exposing a new field through the existing `/drivers/config`-style settings endpoint the client already polls, which is real additional surface a "lightweight, no migration" scope explicitly excluded. Flagged here rather than silently expanded.

## 4. Risk & impact on existing functionality

- Blast radius: 3 files, all backend, all gated by one setting that defaults to the pre-existing behavior. Grepped both files for every other call site constructing an iOS `aps` payload for a ride-offer/dispatch push — `_deliver_push_now` (features.py, the primary immediate-send path) and the push-retry-queue builder (push_retry.py, used only when the immediate send fails and gets queued) are the only two; both are now updated identically so they can't drift.
- With the flag at its default (`False`), the generated `messaging.Aps.sound` value is the exact same string it was before this change — byte-for-byte unchanged wire payload. Confirmed by reading the ternary: the `else` branch is the literal pre-existing string.
- The risk is entirely in the *unflipped* future: enabling `IOS_CRITICAL_ALERTS_ENABLED` before the entitlement is live and shipped would (per Apple's documented behavior for critical alerts without the entitlement) put every iOS driver's ride-offer push at risk of rejection/failure — i.e. this flag is a loaded gun aimed at the entire iOS dispatch-push path, not an isolated feature toggle. The code comments and this log say so explicitly at the point of use.
- No change to Android push behavior, to any ride state, or to any money/wallet path.

## 5. User-experience effect

None today — `IOS_CRITICAL_ALERTS_ENABLED` defaults `False` and nothing reads or writes it elsewhere yet, so this ships fully dark. Only takes effect if a human deliberately flips the setting later, at which point it becomes visible to iOS drivers as a louder, DND-bypassing ride-offer alert (a real, intentional UX change to document in a follow-up Change Impact Log entry *at that time*, not this one).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/core/config.py` | Added `IOS_CRITICAL_ALERTS_ENABLED: bool = False` with a comment on its two hard prerequisites | Single source of truth for the gate, defaulting to current (safe) behavior |
| `backend/features.py` | `_deliver_push_now`'s iOS dispatch `aps.sound` now branches on the setting | Primary push-send path |
| `backend/utils/push_retry.py` | Matching branch in the retry-queue push builder | Keep the retry path from drifting out of sync with the primary path |
| `docs/change-log/2026-09-04-ios-critical-alerts-scaffolding.md` | New change-log entry | CLAUDE.md mandatory Change Impact Log for a dispatch-domain change |

## 7. Before / after

```python
# Before (backend/features.py, _deliver_push_now)
_apns_payload = messaging.APNSPayload(
    aps=messaging.Aps(
        alert=messaging.ApsAlert(title=title, body=body),
        sound="ride_offer.caf",
        category="ride-offer",
        content_available=True,
        mutable_content=True,
    ),
)
```

```python
# After
_apns_payload = messaging.APNSPayload(
    aps=messaging.Aps(
        alert=messaging.ApsAlert(title=title, body=body),
        sound=(
            messaging.CriticalSound(name="ride_offer.caf", critical=True, volume=1.0)
            if settings.IOS_CRITICAL_ALERTS_ENABLED
            else "ride_offer.caf"
        ),
        category="ride-offer",
        content_available=True,
        mutable_content=True,
    ),
)
```
(`push_retry.py`'s equivalent is the same branch nested inside its existing `if is_dispatch else "default"` ternary.)

## 8. Rollback plan

`IOS_CRITICAL_ALERTS_ENABLED` defaults to `False` in code — nothing to roll back today. If it's ever flipped `True` in an env var/deploy config and needs reverting, unset it (or set it back to `false`) and redeploy; no data was written, no migration involved, no Stripe/wallet/ride-state touched. `git revert` alone is also sufficient since the setting doesn't persist any state.

## 9. Verification performed

- [x] `python3 -c "import ast; ast.parse(...)"` on all 3 edited backend files — clean parse (no syntax errors) for `core/config.py`, `features.py`, `utils/push_retry.py`.
- [x] Verified `firebase_admin.messaging.CriticalSound(name, critical, volume)` is a real, documented class in the Python Admin SDK via web search (not from memory alone) before writing code against it — confirmed present as of firebase-admin's current API docs.
- [x] Confirmed via web search that Google Play/Apple developer community reports describe unentitled critical-alert pushes as failing/being rejected by APNs, which is why the setting defaults `False` and both call sites carry an explicit warning comment.
- [x] Grepped both `features.py` and `push_retry.py` for every `messaging.Aps(` construction site to confirm only these two needed the change (§4).
- [x] Traced the default-`False` path by hand: the ternary's `else` branch reproduces the exact pre-existing literal string, so the wire payload is unchanged at default.
- [ ] `python3 -c "from firebase_admin import messaging; ..."` live signature check — **attempted, not completed**: `pip install firebase-admin` failed in this session (`ERROR: Could not find a version that satisfies the requirement... No matching distribution found` — this environment's package-registry egress appears blocked, the same class of failure as `driver-app`'s `yarn install` ECONNRESET failures documented in the sibling change-log entry). Could not run a live import or the rest of the backend's test suite for this change in this session.
- [ ] No automated test added for the branch itself (see §10).

## 10. What was NOT verified

- Could not import `firebase_admin` or run any backend test in this session — this environment's outbound package-registry access did not work for either `yarn` (driver-app) or `pip` (backend) despite multiple attempts; say so explicitly rather than imply a passing test run. Before enabling the flag for real, re-run `cd backend && pip install -r requirements.txt && pytest tests/ -k push` (or equivalent) in a working environment.
- No unit test exercises `IOS_CRITICAL_ALERTS_ENABLED=True` — deliberately: exercising the `True` branch meaningfully would require an actual APNs sandbox round-trip (this repo's `mock_supabase_client`-style unit tests don't reach real APNs), and writing a test that only checks "the `CriticalSound` object was constructed with the right args" would provide close to zero real coverage of the thing that actually matters (whether APNs accepts it) — logged as a gap rather than padded with a low-value test. A real test is worth writing once the entitlement is live and this is actually being turned on, ideally against Apple's sandbox APNs environment.
- Did not attempt the client-side (`notifeeService.ts`) wiring — explicitly scoped out in §3, not an oversight.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (unset an env var; no data involved)
- [x] Blast radius is stated, not assumed (2 call sites in 2 files, both grepped and enumerated; risk is explicitly framed as "loaded for the future," not "safe forever")
- [x] No silent behavior change to an already-shipped flow — default-`False` byte-for-byte-unchanged payload traced by hand in §4/§9
