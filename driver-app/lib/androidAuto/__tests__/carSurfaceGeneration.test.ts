/**
 * lib/androidAuto/carSurfaceGeneration.ts — the remount counter the car map is
 * keyed on, plus the "this instance attached" flag that register.ts's
 * post-connect self-heal consults before remounting.
 *
 * Contract under test: a remount is a fresh native map, so it must clear the
 * ready flag; onMapReady sets it; the self-heal must see `false` for a map that
 * never came up and `true` for one that did.
 */
import {
  bumpCarSurfaceGeneration,
  isCarSurfaceMapReady,
  useCarSurfaceGeneration,
} from '../carSurfaceGeneration';

describe('carSurfaceGeneration', () => {
  beforeEach(() => {
    useCarSurfaceGeneration.setState({ generation: 0, mapReady: false });
  });

  it('starts not ready at generation 0', () => {
    expect(useCarSurfaceGeneration.getState().generation).toBe(0);
    expect(isCarSurfaceMapReady()).toBe(false);
  });

  it('markMapReady flips the flag for the current instance', () => {
    useCarSurfaceGeneration.getState().markMapReady();
    expect(isCarSurfaceMapReady()).toBe(true);
  });

  it('a bump is a new instance: generation +1 and ready cleared', () => {
    useCarSurfaceGeneration.getState().markMapReady();
    bumpCarSurfaceGeneration();
    const s = useCarSurfaceGeneration.getState();
    expect(s.generation).toBe(1);
    expect(s.mapReady).toBe(false);
    expect(isCarSurfaceMapReady()).toBe(false);
  });

  // The self-heal sequence: connect → map attaches → +1.2 s check sees ready →
  // no remount. Then a map that never attached → +4 s check sees not ready →
  // remount → the new instance attaches → ready again.
  it('supports the self-heal decision on both paths', () => {
    useCarSurfaceGeneration.getState().markMapReady();
    expect(isCarSurfaceMapReady()).toBe(true); // attached: skip the remount

    useCarSurfaceGeneration.setState({ mapReady: false });
    expect(isCarSurfaceMapReady()).toBe(false); // never attached: remount
    bumpCarSurfaceGeneration();
    expect(useCarSurfaceGeneration.getState().generation).toBe(1);
    useCarSurfaceGeneration.getState().markMapReady();
    expect(isCarSurfaceMapReady()).toBe(true);
  });
});
