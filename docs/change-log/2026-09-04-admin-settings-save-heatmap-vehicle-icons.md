# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude (session requested by vikas@ngitservices.com) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin (settings-save fix touches the `stripe_secret_key` credential-gate validator; payments-adjacent) |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | Admin-reported bundle: settings save error, blank heat map, vehicle-type icons |

This log covers 3 of the ~7 issues reported in one admin bug-report bundle. The
other 4 (map-style network failure, Driver Deactivation & Appeals policy empty,
Background-Check Consent policy empty, side-nav simplification) are **not**
code-fixable from this investigation and are called out separately in the PR
description with next steps / open questions — they are not included here
because nothing shipped for them.

---

## Fix 1 — Settings → Operations "Save changes" rejected on an unrelated field

### 1. Issue / gap identified
Toggling "Enable refreshed admin theme" (`admin_theme_v2_enabled`) in
Settings → Operations and clicking Save failed with `body.stripe_secret_key:
Value error, stripe_secret_key must start with sk_test_ outside production`
— an error naming a field the admin never touched.

### 2. Root cause
The Settings page (`admin-dashboard/src/app/dashboard/settings/page.tsx`) is
one combined form: every "Save Changes" click PUTs the *entire* settings
object, including whatever masked credential preview (`sk_live_*****`,
`sk_test_*****`) is currently loaded into `stripe_secret_key` from the last
GET. `SettingsUpdateRequest._stripe_secret_key_matches_environment`
(`backend/routes/admin/settings.py`) validates that value's prefix against
the *current* environment *before* `admin_update_settings`'s mask-roundtrip
guard (same file, lines ~672-675) ever runs to drop masked-preview values
from the persisted payload. A masked preview normally happens to pass (its
prefix matches, because it's a preview of the currently-stored key) — but if
the *stored* `stripe_secret_key` doesn't match the current environment's
expected prefix (e.g. a leftover `sk_live_` value sitting in a non-production
`app_settings` row), the preview fails validation and the whole request 422s
— rejecting every field on the page, not just the credential, on every save,
until that stored value is corrected.

### 3. Fix / remediation
`_stripe_secret_key_matches_environment` now exempts any value ending in
`*****` (the masked-preview shape produced by `_mask_credentials`) from the
environment-prefix check, unconditionally. This is safe because the
mask-roundtrip guard downstream always strips any `*****`-suffixed value from
the update regardless of whether it passed validation — exempting it here
only stops an unrelated save from being blocked; it can never let a masked
value overwrite the real stored credential.

### 4. Risk & impact on existing functionality
- **Blast radius: isolated to `stripe_secret_key`'s own field validator.**
  Grepped every reference to `SettingsUpdateRequest` and
  `_stripe_secret_key_matches_environment` repo-wide — no other field's
  validator, and no code outside this one `@field_validator`, depends on the
  old behavior.
- Ran the full `-k settings` backend test subset (291 passed, 1 skipped, 0
  failed) plus the dedicated credential-gate file directly — no regression.
- Does not weaken the real security property the validator exists for
  (rejecting a genuine wrong-environment or attacker-submitted key): a
  non-masked value is still fully validated against the environment prefix,
  exactly as before.

### 5. User-experience effect
Internal-admin-facing only. Before: any Settings save failed whenever the
stored Stripe secret key's prefix didn't match the environment, with an
error naming an unrelated field. After: unrelated saves succeed; editing
`stripe_secret_key` itself is unaffected (still fully validated). Not visible
to riders/drivers/corporate admins. No mid-session surprise for anyone
already using the app — this only affects an explicit admin Save click.

### 6. Files modified
| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/settings.py` | `_stripe_secret_key_matches_environment` now returns early for any `*****`-suffixed (masked-preview) value, before the environment-prefix check | Stop a stored-key/environment mismatch from blocking unrelated settings saves |
| `backend/tests/test_admin_settings_payment_credential_gate.py` | Added `test_stripe_secret_key_masked_preview_with_mismatched_prefix_still_passes_validation` | Regression test for the exact reported scenario |

### 7. Before / after
```python
# Before
if not v:
    return v
if _core_settings.ENV.lower() == "production":
    if not v.startswith("sk_live_"):
        raise ValueError("stripe_secret_key must start with sk_live_ in production")
else:
    if not v.startswith("sk_test_"):
        raise ValueError("stripe_secret_key must start with sk_test_ outside production")
return v
```
```python
# After
if not v:
    return v
if v.endswith("*****"):
    return v
if _core_settings.ENV.lower() == "production":
    if not v.startswith("sk_live_"):
        raise ValueError("stripe_secret_key must start with sk_live_ in production")
else:
    if not v.startswith("sk_test_"):
        raise ValueError("stripe_secret_key must start with sk_test_ outside production")
return v
```

### 8. Rollback plan
Pure code change, no migration, no data touched. Revert the commit and
redeploy — a masked-preview/environment mismatch would once again block
unrelated saves, which is the pre-existing (reported) behavior, not a new
failure mode.

### 9. Verification performed
- [x] Automated tests run: `backend/tests/test_admin_settings_payment_credential_gate.py` (22 tests, all pass, including the new one) and the full `-k settings` subset (291 passed, 1 skipped, 0 failed) via `pytest`, real (non-mocked-away) run in an isolated venv.
- [ ] Manual repro steps followed in staging — **not done**; no staging/DB access from this session.
- [x] Blast-radius grep performed: every `SettingsUpdateRequest` / `_stripe_secret_key_matches_environment` reference repo-wide.
- [x] Reviewed against relevant CLAUDE.md convention (money/Stripe credential handling).
- [ ] Feature-flagged — not applicable; this is a validation-bug fix, not new user-visible behavior.

### What was NOT verified
Not exercised against a real Supabase-backed `app_settings` row with an
actual mismatched stored key — verified via `SettingsUpdateRequest`
construction directly (unit level) and the existing `mock_supabase_client`
test fixtures, not a live/staging round-trip through the actual PUT endpoint
with a real HTTP client and real DB state.

---

## Fix 2 — Heat Map tab renders blank on a tile-style load failure

### 1. Issue / gap identified
The Heat Map page's map renders nothing (no basemap, no heat layers, no
error) when the tile provider is unreachable.

### 2. Root cause
`admin-dashboard/src/components/heat-map.tsx` creates its own MapLibre
instance and gates all layer/data setup inside the `"load"` event, but has no
`map.on("error", ...)` handler at all (unlike the sibling
`monitoring-map.tsx`, which does and shows "Failed to load map style..."). If
the style fetch to the tile host fails, `"load"` never fires, nothing errors
visibly, and the container is left an empty div.

Root network cause (why the tile fetch fails at all — confirmed via `curl`
from this sandbox: `tiles.openfreemap.org` returns a `403 connect_rejected /
organization policy` through this environment's egress proxy) is **not**
fixed by this change — that's an external reachability question raised
separately in the PR description, not a frontend code defect.

### 3. Fix / remediation
Added the same `map.on("error", ...)` → `loadError` state → visible fallback
UI pattern already used by `monitoring-map.tsx`, so a future tile-provider
failure shows "Failed to load map style. Check network / tile provider."
instead of a silent blank tab.

### 4. Risk & impact on existing functionality
- **Blast radius: isolated to `HeatMap`'s own render** — no other component
  imports internals of this file beyond the existing `HeatMapProps` (grepped
  for `from "@/components/heat-map"` / `<HeatMap`; only
  `app/dashboard/heatmap/page.tsx` renders it, unchanged here).
- Purely additive: existing success-path rendering (load → add sources/layers
  → apply data) is untouched; only a new error branch was added.
- Verified with a full `npm run build` (production build, Turbopack) — no
  new type errors, `/dashboard/heatmap` compiles.

### 5. User-experience effect
Internal-admin-facing only (Heat Map page). Before: a tile-provider failure
looked identical to "no data yet" — a blank map, no signal to the operator.
After: an explicit error message, matching the pattern already used
elsewhere on the same page family (Live Monitoring). No behavior change on
the success path.

### 6. Files modified
| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/components/heat-map.tsx` | Added `loadError` state, `map.on("error", ...)` handler, `cancelled` guard on the init effect's cleanup, and an error-state render branch | Surface tile-load failures instead of a silent blank map |

### 7. Before / after
```tsx
// Before
return (
    <div ref={containerRef} style={{ height, width: "100%", borderRadius: "8px", overflow: "hidden" }} />
);
```
```tsx
// After
if (loadError) {
    return (
        <div className="flex items-center justify-center bg-muted" style={{ height, width: "100%", borderRadius: "8px" }}>
            <p className="text-sm text-destructive">Failed to load map style. Check network / tile provider.</p>
        </div>
    );
}
return (
    <div ref={containerRef} style={{ height, width: "100%", borderRadius: "8px", overflow: "hidden" }} />
);
```

### 8. Rollback plan
Pure additive frontend change, no data/migration. Revert the commit and
redeploy — restores the prior (reported) silent-blank behavior, no other
side effect.

### 9. Verification performed
- [x] Production build (`npm run build`) — succeeded, no new errors.
- [x] `tsc --noEmit` — clean.
- [ ] Manual browser repro — **not done**; this environment's own egress
  block to `tiles.openfreemap.org` is a real, reproducible way to trigger
  the error path, but no interactive browser session was used to visually
  confirm the rendered fallback (rider-app/driver-app/admin-dashboard have
  no automated visual-regression tooling for a change like this beyond the
  6 baseline-seeded pages, and Heat Map is not one of the 6 seeded pages
  per CLAUDE.md, so this change carries no visual-regression CI signal
  either way).

### What was NOT verified
Not screenshotted / not run against a live browser session. Reasoned about
via code symmetry with `monitoring-map.tsx`'s already-shipped identical
pattern, not visually confirmed.

---

## Fix 3 — Vehicle Types cards: generic icon + raw slug text instead of a per-type icon

### 1. Issue / gap identified
Every Vehicle Types card showed the same generic `lucide-react` `Car` glyph
followed by the raw Ionicons name string (e.g. `car-sport`, `bus-outline`) as
plain text — inconsistent and, per the report, "not professional." Premium's
large card illustration also appeared missing/blank.

### 2. Root cause
`admin-dashboard/src/app/dashboard/vehicle-types/page.tsx` never mapped the
`icon` field (an Ionicons name consumed by the mobile apps) to any
lucide-react icon for the dashboard's own preview — it only ever rendered a
hardcoded `Car` icon plus the raw string. Separately, the large "3D car
illustration" per card is an admin-uploaded image
(`illustration_url`/`image_url`); the seed script
(`backend/seed_vehicle_types.py`) never sets one for any type, so any type
without an uploaded image falls back to a generic placeholder icon — Premium
specifically has no uploaded illustration in the reporter's environment. That
half is a **content gap, not a code bug** — no fix ships for it here beyond
making the fallback look more intentional (see below).

### 3. Fix / remediation
- Added a small `VEHICLE_ICON_MAP` from the known seeded Ionicons names
  (`car-compact`, `car-sport`, `bus`, `bus-outline`) to a same-shaped
  lucide-react icon (`Car`, `CarFront`, `Bus`, `BusFront`), used both in the
  small icon+label row and as the large-card fallback when no illustration
  image is uploaded. An unrecognized/custom icon string (the field is free
  text in the edit form) still falls back to the generic `Car` icon —
  unchanged from before.
- Replaced the raw slug text (`car-sport`) with a humanized label ("Car
  Sport") via a small `vehicleIconLabel()` helper; the raw value is kept as
  a `title` tooltip.
- Did **not** add or generate any illustration artwork — uploading real
  per-type art remains an admin content task via the existing upload flow.

### 4. Risk & impact on existing functionality
- **Blast radius: isolated to this one page's own rendering.** `icon` is
  otherwise only read by the mobile apps' own `<Ionicons name={...}>` calls
  (`driver-app/`, `rider-app/`, `frontend/`) — none of those import from or
  depend on this admin-dashboard file; grepped for other consumers of
  `VehicleType`/`vt.icon` in `admin-dashboard/` and found none besides this
  file.
- No data/schema change — `icon` is still stored and edited as free text
  exactly as before; this is a display-only mapping.
- Verified with a full `npm run build` — `/dashboard/vehicle-types` compiles
  with no new type errors.

### 5. User-experience effect
Internal-admin-facing only (Vehicle Types page). Visually, the 4 seeded
types now show a distinct, shape-appropriate icon instead of one generic
icon + raw text; Premium's fallback placeholder (while no illustration is
uploaded) is now a car-shaped icon matching its `car-sport` type instead of
the same undifferentiated glyph every other unset type would also show. No
change to any editable data, no change mid-session (this is a static list
page with no live/mid-session state).

### 6. Files modified
| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx` | Added `VEHICLE_ICON_MAP` + `vehicleIconLabel()`; used both in the large-card fallback and the small icon+label row | Render a per-type icon instead of one generic icon + raw slug text |

### 7. Before / after
```tsx
// Before
<span className="flex items-center gap-1">
    <Car className="h-3.5 w-3.5" />
    {vt.icon}
</span>
```
```tsx
// After
<span className="flex items-center gap-1" title={vt.icon}>
    {(() => {
        const TypeIcon = VEHICLE_ICON_MAP[vt.icon] || Car;
        return <TypeIcon className="h-3.5 w-3.5" />;
    })()}
    {vehicleIconLabel(vt.icon)}
</span>
```

### 8. Rollback plan
Pure additive/display-only frontend change, no data touched. Revert the
commit and redeploy — restores the prior generic-icon-plus-raw-text display,
no other side effect.

### 9. Verification performed
- [x] Production build (`npm run build`) — succeeded, no new errors.
- [x] `tsc --noEmit` — clean.
- [x] Confirmed all 4 seeded icon names (`car-compact`, `car-sport`, `bus`,
  `bus-outline`) resolve to distinct, existing `lucide-react` components
  (checked via a Node script against the installed package).
- [ ] Manual browser/visual repro — **not done**. `dashboard-vehicle-types`
  is not one of the 6 baseline-seeded pages in the admin-dashboard's active
  Playwright visual-regression suite (`login`, `dashboard-home`,
  `dashboard-drivers`, `dashboard-monitoring`, `dashboard-settings`,
  `dashboard-rides`), so this change carries no visual-regression CI signal
  either way — reasoned about via the build output and a manual read of the
  rendered JSX, not screenshotted.

### What was NOT verified
Not screenshotted in a live browser. No illustration artwork was
sourced/generated for Premium (or any type) — that gap is unaddressed by
this change and remains an admin content task.
