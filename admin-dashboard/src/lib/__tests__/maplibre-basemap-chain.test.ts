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
  cartoStyleUrl,
  MAP_STYLE_CARTO_DARK,
  MAP_STYLE_CARTO_LIGHT,
  MAP_STYLE_DARK,
  MAP_STYLE_URL,
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
    // OpenFreeMap + (optional Protomaps) + Carto
    expect(chain.length).toBeGreaterThanOrEqual(2);
    expect(chain.length).toBeLessThanOrEqual(3);
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
