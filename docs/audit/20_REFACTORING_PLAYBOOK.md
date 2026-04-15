# Spinr Refactoring Playbook

**Purpose:** This document is the operational guide for the refactoring work
derived from the security, validation, and modularity audits (docs 16-18).

It is opinionated. Follow it so the codebase converges on one style rather
than diverging across authors.

---

## 1. Guiding Principles

1. **Ship working code, not big-bang rewrites.** Each refactor lands in a
   focused PR that leaves the system runnable. No multi-week branches.

2. **Tests before changes.** If you're about to extract or move logic that
   isn't covered by tests, add the tests first. Otherwise you can't know
   the refactor preserved behavior.

3. **Pattern once, migrate many.** The first of each kind (first service,
   first extracted hook, first broken-up screen) is done carefully and
   documented. Everything after follows the pattern.

4. **Preserve imports for as long as practical.** Add new names alongside
   old ones; switch consumers gradually. Use barrel files and re-exports
   so the change log is a diff, not an earthquake.

5. **Delete in a separate PR from moving.** A move-and-delete PR is hard
   to review. Move first, confirm green, then delete the old location.

6. **Every refactor must have a rollback plan.** Usually this is "revert
   the PR." If it's more complicated than that, split the PR.

---

## 2. Prioritized Order

Run top-to-bottom. Do not skip ahead — each tier unlocks the next.

### Tier 0 — Safety (do immediately, not optional)

These are the CRITICAL findings from the security audit. They block
everything else.

| # | Action | File(s) | ~Effort |
|---|--------|---------|---------|
| 0.1 | Remove hardcoded `admin123` default | `backend/core/config.py:40-41` | 30 min |
| 0.2 | Rotate exposed Firebase API key + scrub from git | `rider-app/google-services.json`, `GoogleService-Info.plist` | 1 hr |
| 0.3 | Switch Stripe integration to client-side tokenization | `rider-app/app/manage-cards.tsx`, `backend/routes/payments.py:266-274` | 1 day |
| 0.4 | Remove dev OTP "1234" from login UI + gate backend fallback | `rider-app/app/login.tsx:175-178`, `backend/routes/auth.py:57-60` | 1 hr |
| 0.5 | Remove token-prefix logging | `shared/api/client.ts:200-202` | 15 min |
| 0.6 | Require Stripe webhook signature (fail hard, not warn) | `backend/routes/webhooks.py:33-35` | 30 min |
| 0.7 | Add status validation to ride-completion/start/arrive | `backend/routes/drivers.py:1088-1340` | 4 hr |
| 0.8 | Add `amount: float = Field(..., gt=0)` to payment schema | `backend/routes/payments.py`, `backend/schemas.py` | 30 min |
| 0.9 | Set Fly.io `min_machines=1` and `--workers 4` | `fly.toml`, `backend/Dockerfile` | 15 min |
| 0.10 | Fix Render Python version to 3.12 | `render.yaml` | 15 min |

### Tier 1 — Foundation (this week)

Establish the shared primitives that later refactors depend on.

| # | Action | Status |
|---|--------|--------|
| 1.1 | Fix `shared/package.json` — proper `name`, `exports`, `sideEffects` | ✅ Done |
| 1.2 | Extract `shared/validators/` with unit tests | ✅ Done |
| 1.3 | Scaffold `backend/services/` with `FareService` as reference | ✅ Done |
| 1.4 | Add `.eslintrc` rule: max 500 LOC warn, 800 LOC error | ⏳ |
| 1.5 | Add `madge` / `dependency-cruiser` to CI (circular-dep guard) | ⏳ |
| 1.6 | Migrate rider-app `profile-setup.tsx` email/phone to shared validators | ⏳ |
| 1.7 | Migrate driver-app `profile-setup.tsx` email to shared validators | ⏳ |
| 1.8 | Update README.md — remove stale `/frontend` references | ⏳ |

### Tier 2 — Backend service layer (next 2 weeks)

In the order they should be done. Each gets its own PR.

| # | Service | Extracted from | Why this order |
|---|---------|----------------|----------------|
| 2.1 | `DispatchService` | `routes/rides.py:78-170` (`match_driver_to_ride`) | Highest-risk business logic, most-reused |
| 2.2 | `RideService` | `routes/rides.py`, `routes/drivers.py` (state machine) | Needed for the CRITICAL state-machine fixes |
| 2.3 | `PaymentService` | `routes/payments.py`, `routes/webhooks.py` | Needed for PCI fix + webhook idempotency |
| 2.4 | `DriverService` | `routes/drivers.py` (availability, earnings, location) | Unblocks the 2,230-LOC file |
| 2.5 | `NotificationService` | `features.py` (push, SMS, emergency) | Enables fixing the "emergency SMS not sent" bug |

### Tier 3 — Mobile decomposition (weeks 3-4)

Same pattern as Tier 2 but for screens. Each god screen becomes:
- 1 screen file (< 400 LOC, mostly composition)
- N custom hooks (logic, effects, subscriptions)
- N components (presentational, reusable)

| # | Screen | LOC | Split into |
|---|--------|-----|-----------|
| 3.1 | `rider-app/app/driver-arriving.tsx` | 1,200 | `useDriverArrival` hook + `DriverArrivingMap`, `DriverArrivingPanel` components |
| 3.2 | `rider-app/app/ride-options.tsx` | 1,041 | `useRideEstimate` hook + `VehicleSelector`, `PricingDisplay`, `PromoBanner`, `DateTimePicker` |
| 3.3 | `rider-app/app/search-destination.tsx` | 820 | `usePlaceSearch` hook + `SearchResults`, `RecentSearches` |
| 3.4 | `rider-app/app/payment-confirm.tsx` | 661 | `useRideBooking` hook + `PaymentMethodList`, `FareBreakdown` |
| 3.5 | `driver-app/app/driver/profile.tsx` | 1,233 | `useProfileEditor` hook + `DocumentList`, `VehicleDetails`, `BankInfo` |
| 3.6 | `driver-app/hooks/useDriverDashboard.ts` | 649 | Split into `useActiveRide`, `useEarnings`, `useDriverStatus` |

### Tier 4 — Consolidation (weeks 5-6)

Longer-tail work that's not urgent but pays dividends.

- Integrate `admin-dashboard` with `@shared/*` (starting with API client + types)
- Complete `/frontend` → `rider-app`+`driver-app` migration, remove from CI, delete folder
- Generate TypeScript types from Pydantic schemas (`datamodel-code-generator`)
- Move `rideStore` and `driverStore` commonalities into `@shared/store`
- Add `.prettierrc` + `.editorconfig` across packages

---

## 3. Patterns

### 3.1 Backend service

See `backend/services/README.md` for the full contract. In brief:

```python
# backend/services/ride_service.py
class RideService:
    def __init__(self, db):
        self.db = db

    async def complete_ride(self, ride_id: str, driver_id: str) -> dict:
        ride = await self.db.rides.find_one({
            "id": ride_id,
            "driver_id": driver_id,
            "status": {"$in": ["driver_arrived", "in_progress"]},  # the fix
        })
        if not ride:
            raise InvalidStateException("Ride not in completable state")
        # ... rest of the logic
```

```python
# backend/routes/drivers.py — thin route
@api_router.post("/drivers/rides/{ride_id}/complete")
async def complete_ride(ride_id: str, current_user: dict = Depends(get_current_driver)):
    return await RideService(db).complete_ride(ride_id, current_user["id"])
```

### 3.2 Rider / driver app hook extraction

Target shape:

```tsx
// hooks/useRidePolling.ts
export function useRidePolling(rideId: string | undefined) {
  const [ride, setRide] = useState<Ride | null>(null);
  useEffect(() => {
    if (!rideId) return;
    let cancelled = false;
    const fetchOnce = async () => {
      try {
        const r = await api.get(`/rides/${rideId}`);
        if (!cancelled) setRide(r.data);
      } catch (err) {
        // log via shared logger, don't swallow silently
      }
    };
    fetchOnce();
    const t = setInterval(fetchOnce, 15_000);
    return () => { cancelled = true; clearInterval(t); };
  }, [rideId]);
  return ride;
}
```

Then every screen that was doing its own polling becomes one line:

```tsx
const ride = useRidePolling(rideId);
```

### 3.3 Component extraction

A presentational component:

- Takes `props`, not `params` from the router
- Doesn't call `api.*` directly (take data as props)
- Doesn't touch `router.*` (expose callbacks instead)
- Has a single responsibility (rendering one concept)

```tsx
// components/VehicleSelector.tsx
type Props = {
  estimates: Estimate[];
  selectedIndex: number;
  onSelect: (i: number) => void;
};
export const VehicleSelector = memo(function VehicleSelector({
  estimates,
  selectedIndex,
  onSelect,
}: Props) {
  // pure rendering
});
```

### 3.4 Commit style

- `fix:` — user-visible bug fix
- `feat:` — new capability
- `refactor:` — behavior-preserving change
- `test:` — tests only
- `docs:` — docs only
- `chore:` — tooling / CI
- `perf:` — measurable speedup

Every refactor PR includes:

- The relevant audit ID(s) in the body (e.g. `Fixes C-RIDE-01`)
- A before/after LOC count when meaningful
- A rollback note if not obvious

---

## 4. Per-Refactor Checklist

Copy this into each PR description.

```
## Refactor: <short name>

- [ ] Linked audit ID(s):
- [ ] New tests added, or existing tests updated
- [ ] Pure logic extracted has 80%+ coverage
- [ ] No behavior change (if refactor) or tests for new behavior (if feat)
- [ ] All imports still resolve (run `tsc --noEmit` / `python -m py_compile`)
- [ ] No new `as any`, `# type: ignore`, or `eslint-disable` added
- [ ] LOC change:  before ____   after ____
- [ ] Rollback plan: revert this PR

## Verification
- [ ] Unit tests pass locally
- [ ] `madge --circular` reports no new cycles
- [ ] Manual smoke test of the affected user flow
```

---

## 5. Success Metrics

Track these in the `docs/audit/daily/` folder weekly to prove progress.

| Metric | Today | Target (8 weeks) |
|--------|-------|------------------|
| `drivers.py` LOC | 2,230 | < 300 |
| `rides.py` LOC | 1,501 | < 300 |
| Rider-app screens > 500 LOC | 5 | 0 |
| Rider-app custom hooks | 1 | 15+ |
| Rider-app reusable components | 1 | 30+ |
| Backend service files | 1 | 6+ |
| `admin-dashboard` @shared imports | 0 | 20+ |
| CRITICAL audit findings open | 39 | 0 |
| HIGH audit findings open | 45 | < 10 |
| Test files under `backend/tests/services/` | 1 | 6+ |
| Legacy `/frontend` references in CI | 3 | 0 |

---

## 6. When in Doubt

- **Don't refactor code you don't understand.** Read the tests first. If there
  are no tests, add one pinning current behavior, then refactor.
- **Don't combine refactor with feature work.** One PR, one purpose.
- **Ask for a second pair of eyes on ride-state or payment changes.** Those
  are the two domains where silent bugs cost real money.
- **If you're more than ~500 lines into a PR, stop.** Either land what you
  have or split it.

---

## 7. References

- `docs/audit/17_RIDER_APP_BACKEND_DEEP_AUDIT.md` — full finding list
- `docs/audit/18_MONOREPO_MODULARITY_AUDIT.md` — modularity findings
- `docs/audit/19_FINAL_AUDIT_SUMMARY.md` — executive summary + roadmap
- `backend/services/README.md` — service-layer contract
- `shared/validators/index.ts` — canonical validator API
