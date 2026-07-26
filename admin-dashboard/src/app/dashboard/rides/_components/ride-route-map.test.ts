import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  buildStraightRouteGradient,
  ROUTE_PIN_COLORS,
} from '@spinr/shared/constants/routeMapStyle';

const componentDirectory = path.resolve(__dirname);
const mapSource = fs.readFileSync(path.join(componentDirectory, 'ride-route-map.tsx'), 'utf8');
const detailSource = fs.readFileSync(path.join(componentDirectory, 'ride-detail-modal.tsx'), 'utf8');

describe('admin route replay contract', () => {
  it('draws the uniform straight pickup→dropoff gradient from the shared spec', () => {
    expect(mapSource).toContain('buildStraightRouteGradient');
    expect(mapSource).toContain('ROUTE_PIN_COLORS');
    // The blue per-phase route layers must be gone — no provenance-specific strokes.
    expect(mapSource).not.toContain('#2563eb');
    expect(mapSource).not.toContain('pickupTrail');
    expect(mapSource).not.toContain('toGeoJsonMultiLineString');
  });

  it('uses the shared green pickup / red dropoff / amber completion pins', () => {
    expect(mapSource).toContain('ROUTE_PIN_COLORS.pickup');
    expect(mapSource).toContain('ROUTE_PIN_COLORS.dropoff');
    expect(mapSource).toContain('ROUTE_PIN_COLORS.completion');
    expect(ROUTE_PIN_COLORS.pickup).toBe('#10B981');
    expect(ROUTE_PIN_COLORS.dropoff).toBe('#EF4444');
    expect(ROUTE_PIN_COLORS.completion).toBe('#F59E0B');
  });

  it('builds an orange→red straight gradient between the two endpoints', () => {
    const segs = buildStraightRouteGradient([52.13, -106.67], [52.15, -106.6], 8);
    expect(segs.length).toBe(8);
    segs.forEach((s) => expect(s.color).toMatch(/^#[0-9a-fA-F]{6}$/));
    // Blend runs orange (high green channel) → red (low green channel), so the
    // first segment is greener than the last.
    const green = (hex: string) => parseInt(hex.slice(3, 5), 16);
    expect(green(segs[0].color)).toBeGreaterThan(green(segs[segs.length - 1].color));
  });

  it('detail modal feeds the map just pickup + dropoff coords (no phase lines)', () => {
    expect(detailSource).toContain('<RideRouteMap');
    expect(detailSource).toContain('pickupLat={ride.pickup_lat}');
    expect(detailSource).toContain('dropoffLat={ride.dropoff_lat}');
    expect(detailSource).not.toContain('actualSegments={actualSegmentsProp}');
    expect(detailSource).not.toContain('tripTrail=');
  });
});
