# Bounded native GPS capture reordering

| Field | Detail |
|---|---|
| Issue | Delayed background callbacks are excluded from route geometry as clock regressions. |
| Root cause | SQLite assigns sequence numbers on enqueue; overlapping foreground/background callbacks do not arrive in capture order. The investigated ride had 112 such exclusions, with regressions up to four seconds. |
| Fix | Opt-in, timestamp-first ordering permits at most ten seconds of overlap between known native sources. Session and per-source capture high-water marks never move backwards. Existing identity, accuracy, speed, gap, phase and completion checks remain. |
| Alternative | Unconditional timestamp sorting would conceal real clock regressions. Changing device sequences would break durable identity/acknowledgement semantics. Bounded source-aware acceptance changes neither raw data nor upload IDs. |
| Impact | Consumers: route_finalizer.finalize_route (P3 and optional P2 geometry), ride_route_analyzer.analyze_ride_evidence (offline report). Existing callers retain strict behavior until explicitly opted in. No known-forks entry applies. |
| Risk | A first fix from another source could conceal a small wall-clock change within the bound; native monotonic_ms cannot disambiguate because the recorder falls back to wall-clock time. Overlaps longer than ten seconds remain rejected. Missing GPS fixes cannot be recovered. |
| UX | No change with the default-off option. Enabling it can retain more observed route evidence after completion; it does not fix live car-marker rendering or acquisition gaps. |
| Rollback | Keep the option disabled. Raw evidence is immutable. No production records or settings were changed. Integration must provide a default-off database flag before enabling production callers. |
| Verification | Red: two regression assertions reproduced loss of delayed background points. Green: 28 parser contract cases passed; Ruff passed. Ran pytest with --noconftest for this pure module, avoiding unrelated server startup dependencies. |
| Not verified | Full backend suite, production finalization, actual Android/iOS builds and on-device tracking. No mobile visual regression tooling exists. No mobile code changed. |

| File | Change | Why |
|---|---|---|
| backend/utils/route_segments.py | Default-off bounded native-source reorder option | Preserve legitimate overlapping captures without disabling clock checks |
| backend/tests/test_route_segments.py | Incident-pattern, default-off and guard regression tests | Detect lost fixes and guard weakening |
| docs/change-log/2026-09-15-gps-interleaved-capture.md | Change impact record | Review and rollout limits |

Before:
```python
if candidate.captured_at < prior.captured_at:
    reject("clock_regression")
```
After:
```python
if candidate.captured_at < session_high_water:
    if not bounded_native_source_overlap:
        reject("clock_regression")
```
