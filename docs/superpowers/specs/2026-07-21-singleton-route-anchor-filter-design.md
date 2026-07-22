# Singleton Route Anchor Filter Design

Date: 2026-07-21

## Problem

Completed-route reconstruction promotes every non-empty observed fallback into
an OSRM routing anchor. A fallback containing only one coordinate proves
neither movement nor direction. On ride
`103d560e-c596-429e-92cf-035f1eb66041`, one such interior point caused two
inferred connectors of 8.113 km and 6.119 km around a zero-distance section,
creating a large detour in the revision-10 map.

## Decision

- Durable singleton breadcrumbs remain unchanged as audit evidence.
- A raw observed fallback becomes drawable reconstruction evidence only when it
  contains at least two valid coordinates.
- OSRM-matched sections already require at least two valid coordinates and are
  unchanged.
- After a singleton is omitted, the existing chronological reconstruction loop
  connects the preceding and following trustworthy sections directly, keeping
  the displayed route continuous without using the singleton as an anchor.
- Pickup and authorized completion coordinates remain the endpoint guardrails.
- Distance accounting continues to use observed distance only; inferred
  connector distance remains route-quality metadata.

## Failure Handling

If removing singleton anchors leaves no drawable observed section, the existing
endpoint reconstruction and incomplete-route behavior applies. The system does
not silently restore a singleton, flatten provenance, or use the planned route
as actual evidence.

## Verification

A projection regression test creates three chronological observed segments:
two multi-coordinate sections surrounding a single-coordinate section. The
projection must emit only the two multi-coordinate sections, which causes the
existing reconstruction loop to request one direct internal connector rather
than two detours through the isolated point. Existing reconstruction and
finalizer tests must remain green.

