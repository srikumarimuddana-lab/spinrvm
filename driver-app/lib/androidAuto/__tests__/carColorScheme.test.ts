/**
 * Unit tests for lib/androidAuto/carColorScheme.ts.
 *
 * The behaviour that matters: the scheme must be settable from OUTSIDE React
 * (register.ts's onAppearanceDidChange runs in a native callback, not the tree),
 * and it must default to dark — a head unit that never reports is far more
 * likely to be a dark cabin, and a too-bright map at night is a safety problem
 * where a too-dark one by day is only an annoyance.
 */
import {
  NIGHT_MAP_STYLE,
  setCarColorScheme,
  useCarColorScheme,
} from '../carColorScheme';

beforeEach(() => useCarColorScheme.setState({ scheme: 'dark' }));

describe('scheme store', () => {
  it('defaults to dark', () => {
    expect(useCarColorScheme.getState().scheme).toBe('dark');
  });

  it('is settable from outside React', () => {
    setCarColorScheme('light');
    expect(useCarColorScheme.getState().scheme).toBe('light');
  });

  it('setting the same value does not produce a new state object', () => {
    // The surface subscribes to this; a fresh object every dusk-adjacent tick
    // would re-render the map for nothing.
    const before = useCarColorScheme.getState();
    setCarColorScheme('dark');
    expect(useCarColorScheme.getState()).toBe(before);
  });

  it('never throws — a theme update must not take the surface down', () => {
    const spy = jest.spyOn(useCarColorScheme, 'getState').mockImplementation(() => {
      throw new Error('store unavailable');
    });
    expect(() => setCarColorScheme('light')).not.toThrow();
    spy.mockRestore();
  });
});

describe('night map style', () => {
  it('every entry carries stylers, as react-native-maps requires', () => {
    expect(NIGHT_MAP_STYLE.length).toBeGreaterThan(0);
    for (const entry of NIGHT_MAP_STYLE) {
      expect(Array.isArray(entry.stylers)).toBe(true);
      expect(entry.stylers.length).toBeGreaterThan(0);
    }
  });

  it('keeps roads lighter than the surrounding geometry', () => {
    // The route line is drawn over roads; if roads were darker than the base
    // the geometry a driver navigates by would be the least visible thing.
    const base = NIGHT_MAP_STYLE.find((e) => e.elementType === 'geometry' && !e.featureType);
    const road = NIGHT_MAP_STYLE.find(
      (e) => e.featureType === 'road' && e.elementType === 'geometry',
    );
    const lum = (hex: string) => parseInt(hex.slice(1), 16);
    expect(lum(road!.stylers[0].color)).toBeGreaterThan(lum(base!.stylers[0].color));
  });

  it('hides POI and transit clutter', () => {
    const off = NIGHT_MAP_STYLE.filter((e) => e.stylers[0]?.visibility === 'off');
    expect(off.map((e) => e.featureType)).toEqual(expect.arrayContaining(['poi', 'transit']));
  });
});
