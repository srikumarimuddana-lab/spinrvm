/**
 * Brand-splash geometry and timing.
 *
 * These values are shared by two things that must agree exactly or the launch
 * shows a visible jump: the NATIVE splash (baked into the binary from the
 * expo-splash-screen plugin block in app.config.ts) and the FIRST FRAME that
 * BrandSplash paints once JS is up. Change one, change the other.
 *
 * The fractional values are measurements of the real wordmark, printed by
 * `python3 scripts/brand/gen_splash_assets.py` — re-run it and compare if the
 * logo art is ever redrawn.
 */

/**
 * Splash ground. Brand-locked to white rather than theme-aware: BrandSplash
 * renders outside ThemeProvider (it has to paint before the provider mounts),
 * and white matches both the native frame and the login screen, so neither
 * handoff shows a colour seam.
 */
export const SPLASH_BACKGROUND = '#FFFFFF';

/** Wordmark width as a fraction of screen width, capped so it stays a mark and not a poster. */
export const WORDMARK_WIDTH_FRACTION = 0.49;
export const WORDMARK_MAX_WIDTH = 208;
/** Source wordmark art is 768x312 (backend/static/branding/spinr_logo.png). */
export const WORDMARK_SRC_W = 768;
export const WORDMARK_SRC_H = 312;

/** Where the lockup sits vertically — slightly above true centre reads as composed, not sunken. */
export const LOCKUP_CENTER_Y_FRACTION = 0.46;

/** Centre of the bullseye "o" inside the wordmark, as fractions of its box. */
export const O_CENTER_X_FRACTION = 0.3451;
export const O_CENTER_Y_FRACTION = 0.5160;
/** Square crop (in 768-wide source pixels) that mark.png was cut from. */
export const MARK_CROP_PX = 240;
/** On-screen size of the mark in the first frame — matches the native splash's imageWidth math. */
export const MARK_DP = 84;

/** Halo behind the mark. GLOW_PEAK mirrors the generator constant of the same name. */
export const GLOW_DP = 200;
export const GLOW_SETTLED_SCALE = 2.4;
export const GLOW_SETTLED_OPACITY = 0.8;

/** "Driver" descriptor (driver-app only), measured against wordmark width. */
export const DESCRIPTOR_WIDTH_FRACTION = 0.4582;
export const DESCRIPTOR_HEIGHT_FRACTION = 0.1225;
export const DESCRIPTOR_GAP_FRACTION = 0.0707;
/** Transparent margin baked into descriptor-driver.png, as a fraction of its ink width. */
export const DESCRIPTOR_MARGIN_FRACTION = 0.0206;

/** Motion timeline, milliseconds from mount. */
export const SPLASH_TIMING = {
  markMs: 700,
  lettersDelayMs: 380,
  lettersMs: 460,
  descriptorDelayMs: 560,
  descriptorMs: 400,
  taglineDelayMs: 760,
  taglineMs: 360,
  provenanceDelayMs: 900,
  provenanceMs: 400,
  glowMs: 900,
  /** Only after this long do we admit the app is still working (a slow boot). */
  slowBootMs: 2500,
  exitMs: 350,
  /** Safety net: fires slightly after exitMs in case an Animated callback is dropped. */
  exitFallbackMs: 400,
} as const;

/**
 * Minimum time the splash stays up, even when auth/location finish sooner. The
 * intro settles at ~1.3s, so this shows the whole gesture plus a short beat.
 * (driver-app held 3000ms for the old animation; both apps use this now.)
 */
export const SPLASH_MIN_DISPLAY_MS = 1800;

/** How long to wait for the native splash to hide before forcing it. */
export const NATIVE_SPLASH_WATCHDOG_MS = 2500;

/** Copy. */
export const SPLASH_TAGLINE = 'Ride Local · Support Local';
export const SPLASH_PROVENANCE = 'Proudly Canadian';
