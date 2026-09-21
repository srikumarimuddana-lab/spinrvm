# Change Impact & Risk Log — tile server custom domain

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | srikumarimuddana-lab (via Claude Code) |
| Surface(s) | admin-dashboard (basemap pixels only) |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/osrm-tile-server-dns-b61gpu` |
| Related issue or gap ID | — (operational follow-up to 2026-09-13/14 self-hosted basemap work) |

## 1. Issue / gap identified

The `TilesServer` Railway service had no custom domain — the admin dashboard
pointed at the raw `tilesserver-production-14c2.up.railway.app` host, while its
sibling `osrm-backend` service already had `map-spinr.spinr.ca`.

## 2. Root cause

Not a bug. The tile service was stood up on 2026-09-14 and wired to Vercel by
its Railway-generated host; the custom domain was simply never attached. Two
follow-on confusions made it look worse than it was:

- `admin-dashboard/.env.example` and `deploy/tiles/README.md` used
  `maps.spinr.ca` as an illustrative host. That name has never existed (no DNS
  record), yet it is one character from `map-spinr.spinr.ca`, which is real and
  serves something entirely different (OSRM routing).
- A Railway custom domain attaches to exactly one service, so the two services
  can never share a hostname — which is not obvious from the docs as written.

## 3. Fix / remediation

- Attached `tiles-spinr.spinr.ca` to the `TilesServer` service (targetPort 8080),
  matching the existing `map-spinr` / `admin-spinr` / `api-spinr` convention.
- Replaced the non-existent `maps.spinr.ca` placeholder in the two config-facing
  docs with the real host, and added an explicit note distinguishing
  `tiles-spinr.spinr.ca` (pictures) from `map-spinr.spinr.ca` (distance).

## 4. Risk & impact on existing functionality

Blast radius is which pixels render behind the map pins. Nothing about billing,
fares, ride state, dispatch or driver data touches this service.

Consumers of the tile host, all in admin-dashboard:
- `src/lib/map/maplibre-base.ts` — `basemapChain()`, reads
  `NEXT_PUBLIC_MAP_STYLE_URL`. Since 2026-09-14 this is the **only** hop when
  set, so an unreachable tile host means blank admin maps, not degradation.
- `src/app/dashboard/rides/_components/static-route-map.tsx` — no-WebGL raster
  renderer, reads `NEXT_PUBLIC_RASTER_TILE_URL` (derived from the style URL when
  unset).
- `e2e/visual-regression.spec.ts` — stubs the tile host, so the seeded
  `dashboard-monitoring` baseline does not depend on it.

The **real** risk here is ordering, not the change itself. Both Vercel map
variables are already set in Production, so the dashboard is live on this tile
server today. Switching `PUBLIC_URL` on the service before DNS resolves would
make tileserver-gl advertise absolute tile URLs on a host that does not yet
exist — the style would load and every tile would 404, which looks exactly like
a dead basemap. `PUBLIC_URL` is therefore deliberately NOT changed in this
commit; it is a separate step gated on the CNAME going live.

`map-spinr.spinr.ca` is untouched. It was considered and rejected as a target:
the backend lives in a different Railway project (`cooperative-harmony`) and the
primary backend is on Fly.io, so neither can reach OSRM over
`*.railway.internal` private networking — that public hostname is load-bearing
for `OSRM_URL`. There is also no `osrm_url` column in the live `settings` table,
so the documented admin-dashboard override is not actually available as a
cutover escape hatch.

## 5. User-experience effect

None yet, and none visible mid-session when the remaining steps are done in
order. No rider-, driver- or corporate-facing change. Internal admins see the
same basemap at a different origin once the Vercel variables are repointed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/.env.example` | `maps.spinr.ca` → `tiles-spinr.spinr.ca` (4 lines) | The placeholder host does not exist and collides visually with the real OSRM host |
| `deploy/tiles/README.md` | §4 names the real host; added a note distinguishing it from `map-spinr.spinr.ca` | Same, plus recording that one hostname cannot serve both services |
| `deploy/tiles/smoke-test.sh` | Usage comment host updated | Copy-pasteable example should name a host that resolves |

Not modified, deliberately: the `maps.spinr.ca` occurrences in
`static-route-map.test.ts`, `static-route-map.render.test.tsx` and
`maplibre-basemap-chain.test.ts` are arbitrary fixture hostnames with no config
meaning, and the 2026-09-12 change-log entry is a historical record.

## 7. Before / after

```
# Before — admin-dashboard/.env.example
# NEXT_PUBLIC_MAP_STYLE_URL=https://maps.spinr.ca/styles/basemap/style.json

# After
# NEXT_PUBLIC_MAP_STYLE_URL=https://tiles-spinr.spinr.ca/styles/basemap/style.json
```

## 8. Rollback plan

- **Docs**: `git revert`. No runtime effect.
- **Railway domain**: remove `tiles-spinr.spinr.ca` from the TilesServer
  service. The Railway service domain keeps serving throughout, so this is
  non-destructive at any point.
- **If `PUBLIC_URL` is switched and something breaks**: set it back to
  `https://tilesserver-production-14c2.up.railway.app/`. A Railway variable
  change redeploys but does **not** rebuild, so the tile data is untouched and
  recovery is ~1 minute with no image rebuild.
- **If the Vercel variables are repointed and something breaks**: set them back
  and redeploy with build cache off. `NEXT_PUBLIC_*` is inlined at build time,
  so a redeploy is mandatory either way.

No live data is mutated at any step — nothing here is one-way.

## 9. Verification performed

- Railway API confirms the domain is attached, ownership `verified: true`,
  certificate `CERTIFICATE_STATUS_TYPE_VALID`.
- `bash -n deploy/tiles/smoke-test.sh` passes.
- Confirmed via DNS that `maps.spinr.ca` has no record (so nothing depended on
  the old placeholder) and `map-spinr.spinr.ca` resolves to Cloudflare.
- Confirmed `fare_lock_enabled = true` and `fare_distance_basis = 'road'` in
  production, and that `settings` has no `osrm_url` column.

## 10. What was NOT verified

- **No production build was run.** The changed admin-dashboard file is
  `.env.example`, which is not compiled into the bundle, so `npm run build`
  would exercise nothing related to this diff. No `admin-dashboard` source file
  was touched.
- **The new host was not reached end-to-end.** The CNAME does not exist yet, and
  this session's egress proxy blocks both `*.spinr.ca` and `*.up.railway.app`,
  so `deploy/tiles/smoke-test.sh` could not be run against either host from
  here. It must be run by a human once DNS is live, before the Vercel switch.
- **No visual-regression check.** `dashboard-monitoring` has a seeded,
  merge-blocking baseline, but its test stubs the tile host, so a host change
  cannot move that baseline. Reasoned about, not screenshotted.
