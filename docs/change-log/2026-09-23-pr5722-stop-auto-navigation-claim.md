# PR 5722 per-stop automatic navigation claim

Issue: the auto-navigation marker was keyed only by pickup/dropoff leg, so the dropoff claim prevented a new rider-edited or next-stop destination from opening Maps. The fix adds an optional route identity to the existing claim key; callers without one keep their prior key behavior.

Blast radius is limited to `claimAutoNavLeg` and its only production consumer, `ActiveRidePanel`; the panel will key each pending stop by stable stop ID/index and coordinates. Existing pickup and final-dropoff marker storage is unchanged. The driver sees Maps hand off again when the next stop changes; no other user-facing copy changes.

Alternative considered: bypass the claim and relaunch on every render. The route identity keeps the existing cold-start dedupe while allowing only a genuinely new destination to launch.

| File | Change |
|---|---|
| `driver-app/lib/navigation/autoNavigate.ts` | Include optional destination identity in persisted claim key |
| `driver-app/__tests__/lib/autoNavigate.test.ts` | Verify same-stop dedupe and next-stop relaunch |

Before, `ride-1:dropoff` was one permanent claim. After, the claim is `ride-1:dropoff:<route-key>` when route identity is supplied.

Rollback: revert the helper signature and caller; AsyncStorage marker is ephemeral app state and needs no migration. Verification: `jest --runInBand __tests__/lib/autoNavigate.test.ts` passed (9 tests). What was not verified: no physical device or Maps app handoff; driver app has no visual regression tooling.
