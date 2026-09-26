# Known Forks Registry

Some files in this repo are **intentionally** duplicated rather than shared, usually because
one app needs behavior the other must not have by default (e.g. a course-up camera that only
makes sense while driving). That's a legitimate design choice — the risk isn't the fork
existing, it's a fix landing on one side and silently never reaching the other.

**Why this file exists:** the 2026-09-12 ride-experience industry-benchmark audit
(`docs/audit/ride-experience/REPORT.md`, `ROADMAP.md`) found `shared/components/CarMarker.tsx`
and `driver-app/components/CarMarker.tsx` had diverged five separate times, with fixes ported
one-way by hand and no mechanism catching when a port was missed. One of those misses — a
"car drives sideways" bug driver-app fixed for itself on 2026-09-11 — was still live for every
rider-app user a day later, discovered only because an audit happened to look. This registry,
plus the pre-commit check in `.claude/hooks/pre-commit` that reads it, exists so the *next*
one-way fix gets a reminder instead of a silent gap.

**This is a stopgap, not a fix.** A registry entry only nudges a human to check the sibling —
it can't verify the sibling actually needs (or got) the same change. Where a mechanical parity
guard exists (a test that fails on undeclared capability divergence — see the CarMarker entry
below), that guard is the real protection; the registry entry is what tells you the guard
exists and where to find it.

## How to use this file

- **Adding a new intentional fork:** add a row below. Say why it's forked (not just that it
  is), and whether a mechanical parity guard exists yet.
- **Touching a file listed here:** the pre-commit hook will warn (not block) if you stage one
  side without the other. Read the "why forked" column before assuming the change doesn't
  apply to the sibling — most of the time it does.
- **Reconciling a fork** (merging it back into one shared implementation): remove the row here
  once done, and note the reconciling commit/PR in the removal commit message for history.

## Registry

| File A | File B | Why forked | Parity guard | Tracking |
|---|---|---|---|---|
| `shared/components/CarMarker.tsx` | `driver-app/components/CarMarker.tsx` | driver-app needs course-up-camera bearing/heading callbacks (`onBearingChange`, `mapHeadingRef`) that rider-app must NOT get by default — north-up is the correct rider-side convention. Everything else (GPS smoothing, playback buffer, route-snapping, Android rotation animation) should be identical between the two and has drifted only by omission, not by design. | **Landed 2026-09-12**: `shared/components/__tests__/CarMarkerParity.test.ts` (roadmap R11) diffs both files' `CarMarkerProps` interface and fails on any prop divergence not in its `DRIVER_ONLY_PROPS` allowlist. Runs in CI via `rider-app`'s `roots` config. Covers the prop-level API surface only — an internal-logic-only divergence (like R1/R3's) still needs a human to notice via this file's own header comments. | `ACTION_ITEMS.md` C90 (3 prior ports) + C101 (2 more found + this guard, 2026-09-12 audit) |
| `driver-app/app/driver/notifications.tsx` | `rider-app/app/notifications.tsx` | Each app's inbox screen needs its own navigation destinations per `type` (driver: documents/activity/ride-offer/quests/lost-and-found; rider: lost-and-found/chat/ride-completed/driver-arriving) and driver-app is the only one wired to i18n (`t()`) — rider-app's copy is intentionally inline English, matching that screen's existing pattern. Everything else (loading/error/empty three-state split, optimistic mark-as-read + rollback, a fallback detail view for a `type` with no destination screen) is the same underlying UX contract and has already drifted twice by omission: the 2026-08-18 fix (loading vs error vs "all caught up") had to be ported to both by hand, and the 2026-09-14 fix (true optimistic mark-as-read instead of invalidate-and-refetch; a detail modal for unmapped/id-less notification types instead of a silent no-op tap) again touched both files in the same change for the same reason. Driver-app additionally goes through the shared `shared/hooks/queries/notificationQueries.ts` hooks; rider-app hand-rolls the same fetch/mark-read logic with local `useState` instead of adopting that hook — a further, currently-undecided divergence flagged by the 2026-09-14 notification-pipeline audit, not yet reconciled. | None yet — no mechanical parity guard exists. A human (or reviewer agent) has to notice via this row. | `docs/change-log/2026-08-18-lost-found-chat-and-notification-inbox.md`, `docs/change-log/2026-09-14-notifications-tap-jitter-and-detail-view.md` |
| `rider-app/app/referral.tsx` + `backend/routes/users.py` (rider summary) | `driver-app/app/driver/referral.tsx` + `backend/routes/drivers/referrals.py` | Rider and driver Refer & Earn are separate screens over separate backend endpoints with genuinely different reward shapes (rider: referrer/referee split with a rides-required threshold; driver: a single reward after N qualifying rides), so the fork is intentional. But the *response contract* is near-identical and has already drifted silently: both endpoints returned a `referral_link` field that neither screen ever read — the rider backend built it at `/r/{code}` and the driver backend at `/join/{code}`, on a domain (`spinr.app`) that has never resolved. Nothing caught the path mismatch or the dead domain because no test asserts either side's contract against the other. Removed on both sides together 2026-09-14. | None — no mechanical parity guard. This row is the only thing linking the two. | `docs/audit/2026-09-14-spinr-app-phantom-domain-audit.md` |
| `backend/routes/auth.py` | `backend/routes/admin/auth.py` | Rider/driver and admin auth are deliberately separate: different token lifetimes (15 min vs 1 hr), different trust models (CLAUDE.md — admin JWTs carry role/email/modules in claims and are fully trusted; rider/driver role is re-read from `users` on every request), admin-only MFA and break-glass paths, and separate lockout rules. But the **session-provenance and rate-limit-keying plumbing is the same contract in both**, and it has already drifted once by omission: `routes/admin/auth.py` was moved to `get_real_client_ip()` at all 5 of its call sites and pinned by a guard test, while `routes/auth.py` kept slowapi's `get_remote_address()` — recording the Fly edge proxy (`172.16.x.x`) instead of the real client IP into `refresh_tokens.ip` for every rider and driver session, and from there into 7-year admin-rendered `audit_logs` rows. Nothing flagged it because the guard only ever read the admin twin. Fixed 2026-09-21 (PR #5654). | **Landed 2026-09-21**: `backend/tests/test_async_limiter.py`'s `test_admin_auth_does_not_construct_a_sync_slowapi_limiter` and `test_rider_auth_resolves_real_client_ip_not_socket_peer` now each assert, for their own file, that `get_remote_address(` and `request.client.host` are absent and that `get_real_client_ip(request)` appears exactly 5 times. Source-text guards only — they pin the IP-resolution helper, nothing else about these two files. Any other shared auth contract (lockout, token rotation, session revocation) still needs a human to check both. | `docs/change-log/2026-09-21-auth-real-client-ip.md`, `ACTION_ITEMS.md` C131; X8 successor commitment (below) |
| `rider-app/components/Toast.tsx` | `driver-app/components/UnifiedToast.tsx` | The driver copy is the rider toast rebuilt inside driver-app behind `driver_unified_toast_enabled` (migration 490). The user chose on 2026-09-26 to keep it driver-only, with nothing moved to `shared/` and no change to rider-app. By design, the driver copy uses a fixed 60 px top offset instead of the safe-area inset and has an explicit `accessibilityLabel`. Everything else should match: theme colours, the danger styling, the live region, the one-announcement-per-toast effect, swipe to dismiss, and the 1-title-line / 2-message-line layout. A fix to any of those on one side almost certainly applies to the other. | None. No mechanical parity guard exists. | `docs/change-log/2026-09-26-driver-unified-toast-flag.md` |
| `rider-app/store/toastStore.ts` | `driver-app/store/unifiedToastStore.ts` | Same fork as the row above, for the store. By design the driver store has **no de-duplication**, because every call is a new toast as react-native-toast-message did. It also does not clamp text itself: `driver-app/hooks/useToast.ts` clamps before routing. Its `dismiss(id)` only clears the toast it belongs to; the rider store's `dismiss()` is unguarded, which may be worth porting. | None. | `docs/change-log/2026-09-26-driver-unified-toast-flag.md` |

### `routes/auth.py` refresh successor commitment (T11 X8) — rider/driver only, by design

`POST /auth/refresh` accepts an optional `proposed_refresh_token` (a client-committed successor
used to recover a lost rotation response without tripping the reuse cascade). It is gated by
`settings.refresh_successor_commitment_enabled` (migration 463, **default false**). The admin twin
`routes/admin/auth.py` intentionally has **no** equivalent: admin refresh keeps strict rotation,
and `backend/tests/test_refresh_successor_route.py::test_admin_twin_is_untouched` pins that.
Do not port this to admin. Both files still keep exactly 5 `get_real_client_ip(request)` calls.
The flag must not be enabled until the security audit in
`docs/audit/2026-09-24-refresh-successor-commitment-security-note.md` has passed.

## Adding a mechanical parity guard

When a pair above gets a real guard (a test, a lint rule, a build-time diff check), link it
in the "Parity guard" column and keep the row — the registry is still useful as the "why
forked" explanation even once a guard exists to enforce it mechanically.
