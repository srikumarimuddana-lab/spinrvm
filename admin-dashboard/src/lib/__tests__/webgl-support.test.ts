import { afterEach, describe, expect, it, vi } from 'vitest';
import { hasRenderingWebGL } from '../map/webgl-support';

/**
 * Build a fake <canvas> whose WebGL context behaves however the test needs.
 *
 * `readback` is what readPixels writes into the caller's buffer. That single
 * value is the whole point of this probe: everything else about a stubbed
 * context is indistinguishable from a real one.
 */
function stubCanvas(opts: {
  context?: Record<string, unknown> | null;
  readback?: number[];
}) {
  const ctx =
    opts.context === undefined
      ? {
          VERSION: 0x1f02,
          COLOR_BUFFER_BIT: 0x4000,
          RGBA: 0x1908,
          UNSIGNED_BYTE: 0x1401,
          getParameter: () => 'WebGL 2.0 (OpenGL ES 3.0 Chromium)',
          clearColor: () => {},
          clear: () => {},
          readPixels: (
            _x: number,
            _y: number,
            _w: number,
            _h: number,
            _f: number,
            _t: number,
            out: Uint8Array,
          ) => {
            (opts.readback ?? [0, 255, 0, 255]).forEach((v, i) => (out[i] = v));
          },
        }
      : opts.context;

  vi.spyOn(document, 'createElement').mockImplementation(((tag: string) => {
    if (tag !== 'canvas') return {} as HTMLElement;
    return { width: 0, height: 0, getContext: () => ctx } as unknown as HTMLCanvasElement;
  }) as typeof document.createElement);
}

describe('hasRenderingWebGL', () => {
  afterEach(() => vi.restoreAllMocks());

  it('accepts a context that actually paints the requested colour', () => {
    stubCanvas({ readback: [0, 255, 0, 255] });
    expect(hasRenderingWebGL()).toBe(true);
  });

  // THE regression this function exists for. An ad/privacy extension hands back
  // a context that answers getParameter(VERSION) with a convincing string and
  // never draws anything. The old implementation returned Boolean(VERSION) and
  // so said "yes" here — passing on exactly the machines it was written to
  // protect, and leaving the admin a blank map with nothing in the console.
  // Reproduced on a reporting admin's browser 2026-09-14.
  it('rejects a stub that reports a version but renders nothing', () => {
    stubCanvas({ readback: [0, 0, 0, 0] });
    expect(hasRenderingWebGL()).toBe(false);
  });

  it('rejects a context that paints, but not the colour it was told to', () => {
    // A stub returning arbitrary non-zero bytes must not slip through either.
    stubCanvas({ readback: [255, 0, 0, 255] });
    expect(hasRenderingWebGL()).toBe(false);
  });

  it('tolerates colour-space rounding rather than demanding exactly 255', () => {
    stubCanvas({ readback: [2, 253, 1, 255] });
    expect(hasRenderingWebGL()).toBe(true);
  });

  it('rejects a browser with no WebGL context at all', () => {
    stubCanvas({ context: null });
    expect(hasRenderingWebGL()).toBe(false);
  });

  // A partial stub is as fatal as a full one, and likelier to throw than to
  // return a wrong colour — either way the answer must be false, never a crash
  // that takes the whole ride-detail modal down with it.
  it('rejects a context missing any of the methods the probe needs', () => {
    for (const missing of ['getParameter', 'clearColor', 'clear', 'readPixels']) {
      const ctx: Record<string, unknown> = {
        COLOR_BUFFER_BIT: 0x4000,
        RGBA: 0x1908,
        UNSIGNED_BYTE: 0x1401,
        getParameter: () => 'WebGL 2.0',
        clearColor: () => {},
        clear: () => {},
        readPixels: (..._a: unknown[]) => {},
      };
      delete ctx[missing];
      stubCanvas({ context: ctx });
      expect(hasRenderingWebGL(), `missing ${missing}`).toBe(false);
      vi.restoreAllMocks();
    }
  });

  it('returns false instead of throwing when the context throws', () => {
    stubCanvas({
      context: {
        COLOR_BUFFER_BIT: 0x4000,
        RGBA: 0x1908,
        UNSIGNED_BYTE: 0x1401,
        getParameter: () => 'WebGL 2.0',
        clearColor: () => {},
        clear: () => {
          throw new Error('context lost');
        },
        readPixels: () => {},
      },
    });
    expect(() => hasRenderingWebGL()).not.toThrow();
    expect(hasRenderingWebGL()).toBe(false);
  });

  it('leaves readPixels untouched bytes as a rejection, not a pass', () => {
    // readPixels that silently does nothing leaves the caller's zeroed buffer.
    stubCanvas({ readback: [] });
    expect(hasRenderingWebGL()).toBe(false);
  });
});
