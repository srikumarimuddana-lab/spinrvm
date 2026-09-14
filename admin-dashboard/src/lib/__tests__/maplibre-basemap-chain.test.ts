import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// maplibre-base imports maplibre-gl only for its types and NavigationControl;
// none of the helpers under test touch it, and the real module wants a WebGL
// context jsdom does not provide.
vi.mock('maplibre-gl', () => ({
  default: {},
  Map: class {},
  Marker: class {},
  Popup: class {},
  NavigationControl: class {},
}));

import {
  attachBasemapFallback,
  basemapChain,
  primaryMapStyle,
  cartoStyleUrl,
  MAP_STYLE_CARTO_DARK,
  MAP_STYLE_CARTO_LIGHT,
  MAP_STYLE_DARK,
  MAP_STYLE_URL,
  selfHostedStyleUrl,
} from '../map/maplibre-base';

/** Minimal stand-in for the bits of maplibregl.Map attachBasemapFallback uses. */
function fakeMap() {
  const handlers: Record<string, ((e?: unknown) => void)[]> = {};
  return {
    on(event: string, fn: (e?: unknown) => void) {
      (handlers[event] ||= []).push(fn);
    },
    off(event: string, fn: (e?: unknown) => void) {
      handlers[event] = (handlers[event] || []).filter((h) => h !== fn);
    },
    fire(event: string, payload?: unknown) {
      [...(handlers[event] || [])].forEach((h) => h(payload));
    },
    listenerCount(event: string) {
      return (handlers[event] || []).length;
    },
  };
}

type FakeMap = ReturnType<typeof fakeMap>;
const asMap = (m: FakeMap) => m as unknown as Parameters<typeof attachBasemapFallback>[0];

describe('basemap provider chain', () => {
  it('starts at OpenFreeMap and always ends at the keyless Carto style', () => {
    const chain = basemapChain();
    expect(chain[0]).toBe(MAP_STYLE_URL);
    expect(chain[chain.length - 1]).toBe(MAP_STYLE_CARTO_LIGHT);
    // (optional self-hosted) + OpenFreeMap + (optional Protomaps) + Carto
    expect(chain.length).toBeGreaterThanOrEqual(2);
    expect(chain.length).toBeLessThanOrEqual(4);
  });

  it('never repeats a provider — a retry must actually change host', () => {
    expect(new Set(basemapChain()).size).toBe(basemapChain().length);
    expect(new Set(basemapChain('dark')).size).toBe(basemapChain('dark').length);
  });

  it('uses the dark variants at both ends for a dark theme', () => {
    const chain = basemapChain('dark');
    expect(chain[0]).toBe(MAP_STYLE_DARK);
    expect(chain[chain.length - 1]).toBe(MAP_STYLE_CARTO_DARK);
  });

  it('spans at least two independent hosts, so one host outage cannot blank every hop', () => {
    const hosts = [...new Set(basemapChain().map((u) => new URL(u).host))];
    expect(hosts.length).toBeGreaterThanOrEqual(2);
    expect(hosts).toContain('basemaps.cartocdn.com');
  });

  it('cartoStyleUrl needs no API key in either theme', () => {
    expect(cartoStyleUrl()).toContain('cartocdn.com');
    expect(cartoStyleUrl('dark')).toContain('cartocdn.com');
    expect(cartoStyleUrl()).not.toContain('key=');
    expect(cartoStyleUrl('dark')).not.toContain('key=');
  });
});

// deploy/tiles stands up our own tile server; NEXT_PUBLIC_MAP_STYLE_URL is how
// it reaches the dashboard. Before this it was read only by the public tracking
// page, so setting it did nothing for the admin maps — the exact "config, not
// code" promise .env.example already made and did not keep.
describe('self-hosted basemap override', () => {
  const SELF = 'https://maps.spinr.ca/styles/basemap/style.json';
  const SELF_DARK = 'https://maps.spinr.ca/styles/basemap-dark/style.json';

  afterEach(() => vi.unstubAllEnvs());

  it('is absent by default, so an unconfigured deployment is unchanged', () => {
    expect(selfHostedStyleUrl()).toBeNull();
    expect(basemapChain()[0]).toBe(MAP_STYLE_URL);
  });

  it('is the only hop when configured — no third party behind it', () => {
    vi.stubEnv('NEXT_PUBLIC_MAP_STYLE_URL', SELF);
    const chain = basemapChain();
    // Product decision 2026-09-14: once we serve our own basemap it is the only
    // basemap, so admins stop paying an 8s third-party timeout before the map
    // paints. The accepted cost is that our tile server going down blanks the
    // panel rather than degrading to somebody else's tiles.
    expect(chain).toEqual([SELF]);
    expect(chain).not.toContain(MAP_STYLE_URL);
    expect(chain).not.toContain(MAP_STYLE_CARTO_LIGHT);
  });

  it('ignores whitespace-only configuration rather than trying to load it', () => {
    vi.stubEnv('NEXT_PUBLIC_MAP_STYLE_URL', '   ');
    expect(selfHostedStyleUrl()).toBeNull();
    expect(basemapChain()[0]).toBe(MAP_STYLE_URL);
  });

  it('prefers the dark style in dark mode and falls back to the light one', () => {
    vi.stubEnv('NEXT_PUBLIC_MAP_STYLE_URL', SELF);
    vi.stubEnv('NEXT_PUBLIC_MAP_STYLE_URL_DARK', SELF_DARK);
    expect(selfHostedStyleUrl('dark')).toBe(SELF_DARK);
    expect(basemapChain('dark')[0]).toBe(SELF_DARK);

    // Only a light style built? Still better to serve our own than to skip
    // straight to a third party.
    vi.stubEnv('NEXT_PUBLIC_MAP_STYLE_URL_DARK', '');
    expect(selfHostedStyleUrl('dark')).toBe(SELF);
  });

  // A repeated hop is not a fallback: it re-requests the host that just failed
  // and burns a full 8s watchdog window doing it.
  it('does not duplicate a hop when pointed at a provider already in the chain', () => {
    vi.stubEnv('NEXT_PUBLIC_MAP_STYLE_URL', MAP_STYLE_CARTO_LIGHT);
    const chain = basemapChain();
    expect(chain[0]).toBe(MAP_STYLE_CARTO_LIGHT);
    expect(new Set(chain).size).toBe(chain.length);
    expect(chain.filter((u) => u === MAP_STYLE_CARTO_LIGHT)).toHaveLength(1);
  });

  // Four admin maps (driver, geofence, venue, live-ride) hand MapLibre a single
  // style and never retry, so they cannot use basemapChain(). They hard-coded
  // MAP_STYLE_URL, which meant they kept loading a third party even with our own
  // tile server configured — the chain maps switched over and these silently did
  // not. primaryMapStyle() is what closes that gap.
  describe('primaryMapStyle (single-style maps)', () => {
    it('is the third-party default when self-hosting is not configured', () => {
      expect(primaryMapStyle()).toBe(MAP_STYLE_URL);
    });

    it('is the self-hosted style when configured', () => {
      vi.stubEnv('NEXT_PUBLIC_MAP_STYLE_URL', SELF);
      expect(primaryMapStyle()).toBe(SELF);
    });

    it('never returns a third-party style once self-hosting is on', () => {
      vi.stubEnv('NEXT_PUBLIC_MAP_STYLE_URL', SELF);
      for (const theme of [undefined, 'light', 'dark']) {
        expect(primaryMapStyle(theme)).toBe(SELF);
      }
    });
  });

  // The unconfigured chain is not dead code kept for tidiness: an empty chain
  // paints nothing, so a missing or wrongly-scoped NEXT_PUBLIC_MAP_STYLE_URL
  // must still land on a working provider rather than a blank panel. CI relies
  // on this too — it sets no style URL, and visual-regression.spec.ts stubs
  // tiles.openfreemap.org as the first hop.
  it('still spans multiple hosts when self-hosting is NOT configured', () => {
    const chain = basemapChain();
    expect(chain[0]).toBe(MAP_STYLE_URL);
    expect(chain[chain.length - 1]).toBe(MAP_STYLE_CARTO_LIGHT);
    const hosts = [...new Set(chain.map((u) => new URL(u).host))];
    expect(hosts.length).toBeGreaterThanOrEqual(2);
  });
});

describe('attachBasemapFallback', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const chain = ['style://a', 'style://b', 'style://c'];

  it('advances to the next provider when the basemap errors before load', () => {
    const map = fakeMap();
    const onRetry = vi.fn();
    const onExhausted = vi.fn();
    attachBasemapFallback(asMap(map), chain, 0, { onRetry, onExhausted });

    map.fire('error', { error: new Error('Failed to fetch style') });

    expect(onRetry).toHaveBeenCalledWith('style://b', 1);
    expect(onExhausted).not.toHaveBeenCalled();
  });

  // The failure that actually blanked the ride map: tiles that never arrive and
  // never error, so no error event is ever emitted and `load` never fires.
  it('advances when the basemap silently never finishes loading', () => {
    const map = fakeMap();
    const onRetry = vi.fn();
    attachBasemapFallback(asMap(map), chain, 0, { onRetry, onExhausted: vi.fn() }, 8000);

    expect(onRetry).not.toHaveBeenCalled();
    vi.advanceTimersByTime(8000);

    expect(onRetry).toHaveBeenCalledWith('style://b', 1);
  });

  it('reports exhaustion instead of retrying past the end of the chain', () => {
    const map = fakeMap();
    const onRetry = vi.fn();
    const onExhausted = vi.fn();
    attachBasemapFallback(asMap(map), chain, 2, { onRetry, onExhausted });

    map.fire('error', { error: new Error('nope') });

    expect(onRetry).not.toHaveBeenCalled();
    expect(onExhausted).toHaveBeenCalledTimes(1);
  });

  // A working map must never be torn down and restyled under the admin because
  // one tile 404'd after the basemap already rendered.
  it('ignores errors that arrive after load succeeded', () => {
    const map = fakeMap();
    const onRetry = vi.fn();
    const onExhausted = vi.fn();
    attachBasemapFallback(asMap(map), chain, 0, { onRetry, onExhausted });

    map.fire('load');
    map.fire('error', { error: new Error('one tile 404') });
    vi.advanceTimersByTime(60000);

    expect(onRetry).not.toHaveBeenCalled();
    expect(onExhausted).not.toHaveBeenCalled();
  });

  it('only ever advances once per attempt', () => {
    const map = fakeMap();
    const onRetry = vi.fn();
    attachBasemapFallback(asMap(map), chain, 0, { onRetry, onExhausted: vi.fn() });

    map.fire('error', { error: new Error('a') });
    map.fire('error', { error: new Error('b') });
    vi.advanceTimersByTime(60000);

    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  // The banner bug: onRetry was the only signal a caller got, so a UI that lit
  // up "trying another provider…" on a hop had nothing to turn it off when the
  // next provider succeeded. It stayed pinned over a working map for the life
  // of the mount — which is what an admin actually reported seeing, alongside
  // the fallback provider's own attribution proving the map had loaded fine.
  it('signals success so a retry affordance can be cleared', () => {
    const map = fakeMap();
    const onLoaded = vi.fn();
    attachBasemapFallback(asMap(map), chain, 1, {
      onRetry: vi.fn(),
      onExhausted: vi.fn(),
      onLoaded,
    });

    map.fire('load');

    expect(onLoaded).toHaveBeenCalledTimes(1);
    // The attempt index tells the caller *which* provider won.
    expect(onLoaded).toHaveBeenCalledWith(1);
  });

  it('does not signal success when the basemap failed instead', () => {
    const map = fakeMap();
    const onLoaded = vi.fn();
    attachBasemapFallback(asMap(map), chain, 0, {
      onRetry: vi.fn(),
      onExhausted: vi.fn(),
      onLoaded,
    });

    map.fire('error', { error: new Error('dead') });
    vi.advanceTimersByTime(60000);

    expect(onLoaded).not.toHaveBeenCalled();
  });

  it('does not signal success after the watchdog already gave up on this hop', () => {
    const map = fakeMap();
    const onLoaded = vi.fn();
    attachBasemapFallback(asMap(map), chain, 0, {
      onRetry: vi.fn(),
      onExhausted: vi.fn(),
      onLoaded,
    }, 8000);

    vi.advanceTimersByTime(8000);
    // A late `load` from the abandoned map must not clear a banner that now
    // belongs to the *next* attempt.
    map.fire('load');

    expect(onLoaded).not.toHaveBeenCalled();
  });

  it('stays optional — a caller with no success affordance still works', () => {
    const map = fakeMap();
    expect(() => {
      attachBasemapFallback(asMap(map), chain, 0, {
        onRetry: vi.fn(),
        onExhausted: vi.fn(),
      });
      map.fire('load');
    }).not.toThrow();
  });

  it('detaching cancels the watchdog and releases both listeners', () => {
    const map = fakeMap();
    const onRetry = vi.fn();
    const onExhausted = vi.fn();
    const detach = attachBasemapFallback(asMap(map), chain, 0, { onRetry, onExhausted });

    detach();
    vi.advanceTimersByTime(60000);

    expect(onRetry).not.toHaveBeenCalled();
    expect(onExhausted).not.toHaveBeenCalled();
    expect(map.listenerCount('load')).toBe(0);
    expect(map.listenerCount('error')).toBe(0);
  });
});
