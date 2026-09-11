# Change Impact & Risk Log — demand tile rasteriser (B4, unmounted)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-09 |
| Author | mkkreddy52@gmail.com (via Claude Code) |
| Surface(s) | backend (new module only — not mounted on any router) |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/pr-5142-review-5dagsf` — `b1a0af6` |
| Related issue or gap ID | PR #5142 plan task B4; finding HM26-06 (per-viewport normalisation) |

## 1. Issue / gap identified

The driver heatmap cannot look the same on iOS, Android and Android Auto, because only
Android has a native density spreader. Every client-side approximation diverges from it.

## 2. Root cause

The two operations are mathematically different, not merely differently tuned:

```
kernel density:     d(p) = Σ wᵢ · k(|p − cᵢ|)     linear, then scaled
alpha compositing:  a(p) = 1 − ∏ (1 − aᵢ)         saturating
```

Under compositing, two cells of weight 1 and one cell of weight 2 can never look alike;
under a real kernel they are identical. No radius, ring count or alpha fixes that — it is
the wrong operator. A per-cell renderer also colourises *before* compositing, whereas a
real heatmap sums density first and maps colour once.

## 3. Fix / remediation

`backend/services/demand_tiles.py` does the summing once, server-side, and emits pixels
every surface can draw identically: spread through a metre-denominated Gaussian → sum →
normalise against the snapshot's **absolute** scale → one colour-table lookup per pixel →
transparent 256×256 RGBA PNG.

Two defects found and fixed while building it:

- **The plan's fixed 180 m sigma degenerates at the bottom of its own stated native band.**
  At z=10 near 52°N a pixel covers ~94 m, so the sigma spans under 2 px and aliases into
  specks rather than a field. `MIN_SIGMA_PX` lets metres govern wherever resolvable and
  floors it below that, preserving the support:sigma ratio so the 3σ cut does not slice
  into the visible kernel. This is not a fudge — at that zoom the raster genuinely cannot
  carry 180 m of detail.
- **The colour table needs alpha anchored at zero** — the same defect just fixed on
  Android. An opaque lowest entry paints every pixel the kernel touches at all, so the
  field ends at a visible disc edge instead of fading into bare map.

## 3b. Post-review fix — kernel taper (2026-09-09)

PR #5144 review found that the bare 3-sigma truncation left a hard disk edge. The residue at
the cut is ~1.11% of peak, which an earlier comment in this module wrongly called negligible —
it is multiplied by weight and divided by scale *before* becoming alpha, so at weight 500
against scale 5 it normalised to 1.11, clipped to the ceiling, and fell to zero one pixel out.

Measured on the pixel grid:

```
untapered  [82, 82, 82, 82, 82, 0]   worst adjacent step 82, zero intermediate values
tapered    [82, 71, 43, 20,  0, 0]   worst adjacent step 28, three intermediate values
```

`kernel_value()` subtracts the Gaussian's value at the support edge and renormalises, reaching
zero there by construction. Peak stays 1.0. The remaining edge is steep but continuous; at
100x the published scale the field saturates and the gradient compresses into the rim, which
is scale calibration rather than kernel shape. Test thresholds are set from the measured
numbers, not an idealised zero. Review thread:
https://github.com/srikumarimuddana-lab/spinrvm/pull/5144#discussion_r3967262691

## 4. Risk & impact on existing functionality

**Blast radius: none. The module is new and nothing imports it.**

- `grep -rn "demand_tiles" backend/` → only the module and its two test files.
- No router mounts it, no background loop calls it, no migration, no settings key.
- Does not read or write the database, Redis, Stripe, ride state, wallets, or insurance
  period rows.
- Does not touch the existing `GET /drivers/demand-heatmap` endpoint or its v1/v2 payloads.

Deliberately **not** included, and why: authenticated tile sessions (B5) and the serving
route (B6). Those are the security layer — scoped opaque handles, area authorisation,
rate/budget limits, redaction — and shipping a rasterizer alone keeps that review separate
rather than smuggling an access-control surface in alongside a maths change.

## 5. User-experience effect

**Nobody sees anything.** No surface reaches this code. It cannot alter a driver's map, a
rider's flow, or any admin view until B5/B6 mount it behind `driver_heatmap_v3_enabled`.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/demand_tiles.py` | New. Web Mercator projection, halo selection, kernel sizing with pixel floor, RGBA colour LUT, numpy/Pillow raster | One summed, once-colourised field all three surfaces can draw |
| `backend/tests/test_demand_tiles.py` | New. 62 stdlib-only cases | Geometry, seams, scale and LUT stay testable without numpy |
| `backend/tests/test_demand_tiles_render.py` | New. 10 render cases incl. a pure-Python cross-check | Catches broadcasting/dtype bugs the vectorised path could hide |

## 7. Before / after

Not applicable — purely additive, no existing behaviour changed.

The property worth recording is the one guarded by
`test_scale_is_absolute_not_relative_to_what_is_in_view`:

```python
# The mobile renderers divide by the strongest cell currently in the viewport,
# so panning changes what a colour means and a lone weak cell renders darkest.
alone           = snapshot([(SK_LAT, SK_LNG, 4.0)])
with_far_giant  = snapshot([(SK_LAT, SK_LNG, 4.0), (SK_LAT + 4.0, SK_LNG, 900.0)])
assert render(alone, z, x, y) == render(with_far_giant, z, x, y)   # byte-identical
```

## 8. Rollback plan

Delete the module and its tests, or simply never mount it. Nothing to revert at runtime:
no route, no flag consulted, no data written, no schema change. `git revert` is genuinely
sufficient here because nothing has been applied to live data.

## 9. Verification performed

- [x] **62/62 stdlib cases pass**, executed against the real module. Includes projection
      checked against known Web Mercator reference values (world origin, both corners,
      zoom doubling, pole clamping) and tile-centre round-trips exact to 1e-6 px.
- [x] **Mutation-tested the harness itself.** Four deliberate defects — unanchored LUT
      alpha, halo removed, pole clamp removed, kernel floor removed — each produced
      failures; the restored module passes 62/62. The suite is not vacuous.
- [x] `ruff check` and `ruff format --check` clean on all three files.
- [x] Reviewed against `CLAUDE.md`: no money arithmetic, no ride state, no RLS, no PII
      (input is already-suppressed aggregates; nothing is logged). Pre-commit hook passed.

## 10. What was NOT verified

- **`pytest` was never run.** `pypi.org` and `files.pythonhosted.org` both return **403**
  in this environment, so numpy, Pillow and pytest cannot be installed. The 62 stdlib
  cases were executed through a minimal hand-written pytest stand-in (approx / raises /
  parametrize), which is why it was mutation-tested above — but it is not pytest.
- **`test_demand_tiles_render.py` has never executed.** Every assertion about actual
  pixels — the numpy-vs-reference cross-check, alpha ceiling, determinism, seam
  continuity, transparent-empty — is written and unproven. `render_demand_tile()` itself
  has never run.
- **No performance measurement.** The plan's budgets (cached tile p95 < 200 ms, cold
  raster < 1 s) are unmeasured. One known concern: at z=16 the support radius reaches
  ~368 px, wider than the tile, so each cell paints the full 256×256 patch.
- **No service-area polygon mask.** The plan asks that demand not spread outside the
  authorised area. Not implemented — it needs the area context that arrives with B5/B6.
- **Antimeridian wraparound is not handled** in halo selection. Harmless for
  Saskatchewan, wrong for a service area crossing ±180°.
- **`MAX_ALPHA = 0.32`** is the plan's documented value. Reading the Uber reference
  screenshot suggests ~0.42 would match better; the more translucent value was kept
  because keeping the base map readable is the safer default. Worth revisiting with eyes
  on a real render.

## 11. Sign-off

- [x] Rollback plan is concrete (nothing mounted; delete or ignore)
- [x] Blast radius stated and grepped, not assumed
- [x] No behaviour change to any shipped flow — module is unreachable
- [ ] **Not cleared for use.** Needs a real pytest run with numpy/Pillow, then B5/B6 before
      anything can serve a tile.
