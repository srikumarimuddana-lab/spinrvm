/**
 * Unit tests for lib/androidAuto/carMapCamera.ts — the zoom math + shared store
 * that lets the head-unit map buttons drive the (non-interactive) car surface.
 * Pure functions need no head unit; the store is exercised directly.
 */
import {
  MIN_DELTA,
  MAX_DELTA,
  DEFAULT_DELTA,
  clampDelta,
  zoomInDelta,
  zoomOutDelta,
  useCarMapCamera,
} from '../carMapCamera';

describe('zoom math', () => {
  it('zooming in shrinks the span, out grows it', () => {
    expect(zoomInDelta(DEFAULT_DELTA)).toBeLessThan(DEFAULT_DELTA);
    expect(zoomOutDelta(DEFAULT_DELTA)).toBeGreaterThan(DEFAULT_DELTA);
  });

  it('clamps to MIN when zooming all the way in', () => {
    let d = DEFAULT_DELTA;
    for (let i = 0; i < 50; i++) d = zoomInDelta(d);
    expect(d).toBe(MIN_DELTA);
  });

  it('clamps to MAX when zooming all the way out', () => {
    let d = DEFAULT_DELTA;
    for (let i = 0; i < 50; i++) d = zoomOutDelta(d);
    expect(d).toBe(MAX_DELTA);
  });

  it('clampDelta bounds any value and rejects non-finite', () => {
    expect(clampDelta(999)).toBe(MAX_DELTA);
    expect(clampDelta(0)).toBe(MIN_DELTA);
    expect(clampDelta(Number.NaN)).toBe(DEFAULT_DELTA);
  });

  it('a zoom-in then zoom-out round-trips back to the start within range', () => {
    expect(zoomOutDelta(zoomInDelta(DEFAULT_DELTA))).toBeCloseTo(DEFAULT_DELTA, 6);
  });
});

describe('useCarMapCamera store', () => {
  beforeEach(() => useCarMapCamera.getState().reset());

  it('starts at the default span', () => {
    expect(useCarMapCamera.getState().delta).toBe(DEFAULT_DELTA);
  });

  it('zoomIn / zoomOut mutate delta and stay clamped', () => {
    const { zoomIn, zoomOut } = useCarMapCamera.getState();
    zoomIn();
    expect(useCarMapCamera.getState().delta).toBe(zoomInDelta(DEFAULT_DELTA));
    for (let i = 0; i < 50; i++) zoomOut();
    expect(useCarMapCamera.getState().delta).toBe(MAX_DELTA);
  });

  it('reset returns to the default span', () => {
    useCarMapCamera.getState().zoomIn();
    useCarMapCamera.getState().reset();
    expect(useCarMapCamera.getState().delta).toBe(DEFAULT_DELTA);
  });

  it('pan accumulates finite translations', () => {
    useCarMapCamera.getState().pan(70, -35);
    const s = useCarMapCamera.getState();
    expect(s.offsetLng).toBeLessThan(0); // dragged right → view moves west
    expect(s.offsetLat).toBeLessThan(0); // dragged up → view moves south
    expect(Number.isFinite(s.offsetLat) && Number.isFinite(s.offsetLng)).toBe(true);
  });

  // A NaN centre is silently dropped by Google Maps at every entry point, which
  // leaves the head unit at the factory 0,0 / zoom-2 world view. One bad
  // sample must not poison the offset for the rest of the session.
  it('pan ignores non-finite translations instead of poisoning the offset', () => {
    useCarMapCamera.getState().pan(10, 10);
    const before = useCarMapCamera.getState();
    useCarMapCamera.getState().pan(Number.NaN, 5);
    useCarMapCamera.getState().pan(5, Number.POSITIVE_INFINITY);
    useCarMapCamera.getState().pan(undefined as unknown as number, 0);
    const after = useCarMapCamera.getState();
    expect(after.offsetLat).toBe(before.offsetLat);
    expect(after.offsetLng).toBe(before.offsetLng);
  });
});
