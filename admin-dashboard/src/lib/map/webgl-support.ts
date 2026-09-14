// src/lib/map/webgl-support.ts
// Whether this browser can actually render WebGL — not merely whether it says
// it can.
//
// Lives in its own module rather than inside ride-route-map.tsx so it can be
// tested without importing maplibre-gl and its stylesheet, and so the other
// admin maps (live-map, driver-map, geofence-map) can adopt the same probe when
// they gain raster fallbacks.

/**
 * Does a real, drawing WebGL context exist?
 *
 * Privacy and ad-blocking extensions commonly hand back a **stubbed** WebGL
 * context that never throws and never draws. The failure signature is a blank
 * canvas with no evidence anywhere: no failed request, no CSP violation, no
 * console error, and an intact attribution overlay sitting on top of nothing.
 *
 * The previous implementation read `gl.getParameter(gl.VERSION)` and trusted a
 * non-empty answer. That cannot work — returning a plausible version string is
 * exactly what a convincing stub does, so the check passed on precisely the
 * machines it existed to protect, and the map stayed blank. Confirmed on a
 * reporting admin's browser 2026-09-14: `gl.VERSION` answered normally while a
 * clear→readPixels round-trip came back stubbed.
 *
 * The one thing a stub cannot fake is producing pixels. So ask it to: clear to
 * a known colour and read the buffer back. A real context returns that colour.
 *
 * Fails toward `false` on anything it cannot positively verify — a missing
 * method, a throw, an unexpected colour. Being wrong in that direction costs a
 * plainer raster map; being wrong the other way costs an empty panel on the
 * SGI / dispute-review screen.
 */
export function hasRenderingWebGL(): boolean {
    if (typeof document === "undefined") return false;
    try {
        const canvas = document.createElement("canvas");
        // 2x2 is enough to read one pixel and costs nothing to allocate.
        canvas.width = 2;
        canvas.height = 2;
        const gl = (canvas.getContext("webgl2") ||
            canvas.getContext("webgl")) as WebGLRenderingContext | null;
        if (
            !gl ||
            typeof gl.getParameter !== "function" ||
            typeof gl.clearColor !== "function" ||
            typeof gl.clear !== "function" ||
            typeof gl.readPixels !== "function"
        ) {
            return false;
        }
        const pixel = new Uint8Array(4);
        gl.clearColor(0, 1, 0, 1); // pure opaque green
        gl.clear(gl.COLOR_BUFFER_BIT);
        // Valid immediately after clear: the drawing buffer is not discarded
        // until the frame is composited, so preserveDrawingBuffer is not needed.
        gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
        // Tolerant of colour-space rounding, intolerant of "not actually green".
        return pixel[1] > 200 && pixel[0] < 50 && pixel[2] < 50;
    } catch {
        return false;
    }
}
