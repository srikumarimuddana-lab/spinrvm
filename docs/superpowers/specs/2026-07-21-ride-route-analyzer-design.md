# Ride Route Analyzer Design

## Purpose

Build a read-only diagnostic tool that explains how one completed ride's GPS
samples are assigned to lifecycle phases and why a stored or displayed route
distance differs from the passenger trip. The tool must make the July 21 route
problem reproducible without changing ride, fare, route, or breadcrumb rows.

## Approaches Considered

1. **Standalone analyzer with live and offline inputs (chosen).** A small CLI
   calls a pure analyzer and can either load one ride from the configured
   database or consume exported JSON. It is safe to run locally or in a
   production shell and is independently testable.
2. **Admin diagnostic endpoint.** This is convenient for support staff, but it
   increases the authenticated API and PII surface before the calculation has
   been validated.
3. **Dry-run mode inside the route finalizer.** This maximizes code reuse, but
   couples diagnosis to a write-oriented background worker and makes accidental
   mutation harder to rule out.

The standalone analyzer is the smallest safe evidence-gathering step. An admin
endpoint may be designed later after its output contract is stable.

## Inputs

The command supports two mutually exclusive modes:

- `--ride-id <uuid>`: read the ride and its `driver_location_history` rows using
  existing backend database helpers. Reads are ordered and paginated; the mode
  never calls insert, update, delete, RPC, or route-finalization functions.
- `--ride-json <path> --locations-json <path>`: analyze exported JSON without a
  database connection.

The ride must include `ride_requested_at`, `ride_started_at`, and
`ride_completed_at`. GPS capture time is selected from `captured_at`, falling
back to `timestamp` only for legacy rows. Pickup and completion coordinates are
used as endpoint anchors but are never printed to standard output.

## Authoritative Phase Classification

Client-supplied `tracking_phase` is evidence for comparison only. The analyzer
derives the effective phase from the ride lifecycle timestamps:

- **Phase 1:** capture time before `ride_requested_at`.
- **Phase 2:** capture time from `ride_requested_at` inclusive to
  `ride_started_at` exclusive.
- **Phase 3:** capture time from `ride_started_at` through
  `ride_completed_at`, both inclusive.
- **Excluded tail:** capture time after `ride_completed_at`.

Only Phase 3 contributes to the passenger-trip distance and actual-route
geometry. A segment is counted only when both endpoints belong to the same
derived phase, so no distance crosses a lifecycle boundary. For the reported
ride, Phase 2 is expected to be approximately zero because the driver was
already beside the rider.

## Validation and Distance Analysis

The analyzer orders samples by capture timestamp, recording session, and
sequence number. It reports invalid timestamps, invalid coordinates, duplicate
sample identities, clock regressions, unreasonable gaps, and physically
impossible segments using the existing route-integrity limits.

It calculates:

- point and accepted-segment counts for every derived phase;
- Phase 2 and Phase 3 duration;
- filtered observed haversine distance per phase;
- the existing all-ride-linked calculation for comparison;
- strict Phase 3 OSRM distance when an OSRM URL is available;
- contamination delta and ratio between the legacy/all-points result and the
  strict Phase 3 result;
- disagreement counts between stored `tracking_phase` and timestamp-derived
  phase;
- temporal coverage, maximum GPS gap, rejected-segment counts, and endpoint
  coverage.

Fare amounts and booked distance are contextual values only. The analyzer does
not recalculate or propose changing the settled fare.

## Route Geometry

The route artifact contains Phase 3 only. The pickup anchor starts the route,
the completion fix or drop-off anchor ends it, and accepted GPS samples remain
in chronological order. Continuous observed sections are map-matched with OSRM
using ordered timestamps and accuracy radiuses. Missing sections are connected
with bounded OSRM Route requests between the surrounding trusted coordinates.
The tool never connects Phase 1 or Phase 2 geometry into Phase 3 and never draws
a straight line across an unresolved gap.

When every gap is reconstructed, the route is emitted as one uniformly styled
actual-route GeoJSON feature. Observed and inferred proportions remain metadata
for audit but do not create alternating line colors. If OSRM cannot resolve a
gap, the report marks the route incomplete and omits that visual connector
rather than inventing geometry.

## Outputs and Privacy

Standard output is sanitized JSON containing ride ID, lifecycle timestamps,
counts, distances, ratios, durations, rejection reasons, and a diagnosis. It
does not contain raw latitude/longitude, addresses, names, phone numbers, or
email addresses.

An optional `--route-output <path>` writes local Phase 3 GeoJSON. Because that
file contains precise GPS data, the CLI prints a privacy warning, refuses to
overwrite an existing file unless explicitly requested, and does not commit the
artifact. The default command produces no coordinate-bearing file.

## Failure Behaviour

- Missing or invalid lifecycle boundaries fail with a non-zero exit code.
- `ride_completed_at <= ride_started_at` fails loudly.
- Live mode reports database failures and does not fall back to empty data.
- Missing OSRM configuration still produces observed-distance diagnostics and
  reports `osrm_status: unavailable`; it does not claim a complete matched
  route.
- Provider timeout or malformed geometry is reported as an upstream failure;
  raw points remain available for analysis but no fake connector is emitted.
- Empty or unusable GPS evidence produces a valid low-coverage report and a
  non-drawable route diagnosis rather than substituting booked distance as
  actual distance.

## Code Boundaries

The implementation is limited to three files in one logical change:

- `backend/utils/ride_route_analyzer.py`: pure classification, validation,
  distance calculations, diagnosis, and optional asynchronous OSRM projection.
- `backend/scripts/analyze_ride_route.py`: argument parsing, read-only live/JSON
  loading, sanitized output, exit codes, and optional GeoJSON writing.
- `backend/tests/test_ride_route_analyzer.py`: synthetic timestamp-boundary,
  phase-contamination, rejection, privacy, and CLI-contract tests.

The analyzer reuses existing datetime, distance, segmentation, and OSRM helpers
where their contracts match. It does not modify the production finalizer in
this change; the analyzer's evidence will define a separate tested production
fix.

## Verification

Tests must first fail for the missing analyzer, then pass after implementation.
They cover exact boundary inclusivity, Phase 2 near-zero behavior, Phase 3-only
distance, pre-start and post-completion exclusion, stored-phase disagreement,
out-of-order delivery, invalid evidence, no-coordinate console output, offline
JSON operation, and OSRM-unavailable behavior. The focused test file and the
existing route-distance and route-segmentation suites must pass before commit.

