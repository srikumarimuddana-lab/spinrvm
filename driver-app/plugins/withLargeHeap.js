const { withAndroidManifest } = require('@expo/config-plugins');

// Sets android:largeHeap="true" on the <application> element.
//
// Why: this process runs TWO Google Maps GL views at once whenever Android
// Auto is connected (the phone map plus the car surface drawn into a
// VirtualDisplay), on top of Skia, Reanimated and the OSRM live-route
// polylines. On the 2026-09-11 test ride (SPR-T9NYPB) it exhausted the default
// 256 MB Java heap 59 s after a relaunch and died with OutOfMemoryError
// (Sentry CRIMSON-SMOKE-7445-SX, `growth limit 268435456`) on a Pixel 9 Pro XL
// with 15 GB of RAM.
//
// This is a MITIGATION, not the fix. The remount churn that fills the heap is
// addressed in lib/androidAuto (one native car map per connection, no
// unconditional post-connect remounts) and app/driver/(tabs)/index.tsx. The
// larger heap buys headroom so a slow leak becomes a warning on the memory
// graph instead of a mid-trip death. Google's own guidance for navigation-
// class apps with multiple map surfaces is the same flag.
//
// Cost: the per-app heap limit roughly doubles (device-dependent, typically
// 512 MB on flagship class). GC pauses can lengthen with a fuller heap; on
// this app's allocation profile that is not measurable next to a GL map
// re-creation. No effect on iOS. Native change — ships in an EAS build, not
// an OTA update.
const withLargeHeap = (config) => {
    return withAndroidManifest(config, (cfg) => {
        const app = cfg.modResults.manifest.application && cfg.modResults.manifest.application[0];
        if (app) {
            app.$ = app.$ || {};
            app.$['android:largeHeap'] = 'true';
        }
        return cfg;
    });
};

module.exports = withLargeHeap;
