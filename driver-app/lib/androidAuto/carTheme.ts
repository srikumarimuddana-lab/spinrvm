/**
 * Design tokens for the Android Auto surface.
 *
 * Sourced from `shared/theme`'s dark palette — the SAME tokens the phone app
 * renders with — rather than hand-picked hex. The car layer previously invented
 * its own colours (`#ee2b2b` for the brand red, Google's `#0f9d58` for green,
 * `#ff8a00`, `#ffd23f`), which drifted from the product on the surface a driver
 * sees most often. A dashboard that doesn't match the phone reads as a bolted-on
 * companion, not part of the same product.
 *
 * Dark palette unconditionally: the Android Auto surface is always dark, and
 * unlike the phone it has no light mode to respond to.
 *
 * ─── Constraints this file exists to respect ─────────────────────────────────
 * Google's Car app quality guidelines forbid animated elements on a connected
 * head unit, and the review enforces it before a public release. So every sense
 * of quality here has to come from static properties: type scale, hierarchy,
 * contrast, spacing, and restraint. Nothing in this file may imply motion.
 *
 * Type sizes are floored well above phone equivalents — a dashboard is read at
 * arm's length in a moving vehicle, in one glance.
 */
import { darkColors } from '@shared/theme';

export const carColors = {
  /** Spinr brand red. Status pills for active legs, the accent dot. */
  brand: darkColors.primary,
  /** Completed / go states. */
  success: darkColors.success,
  /** Surge. */
  surge: darkColors.orange,
  /** Earnings bonuses and incentives — the "you made extra" signal. */
  gold: darkColors.gold,
  /** WAV and other informational chips. */
  info: darkColors.info,

  /** Card body. Near-opaque so map detail never fights the text. */
  cardBg: 'rgba(18,18,20,0.94)',
  /** Hairline top edge — reads as elevation without a shadow (cheap to raster). */
  cardEdge: 'rgba(255,255,255,0.10)',
  /** Chip / avatar backing. */
  raised: darkColors.surfaceLight,

  text: '#FFFFFF',
  textDim: darkColors.textDim,
  textMuted: darkColors.textSecondary,
} as const;

/**
 * Type scale for a dashboard read at a glance.
 *
 * `hero` is reserved for the one number a driver most wants to see — the fare.
 * Giving earnings the largest type on the screen is the entire point: it is
 * what the work is for, and burying it in a footer row understated it.
 */
export const carType = {
  hero: 46,
  title: 24,
  body: 19,
  label: 15,
  micro: 13,
} as const;

export const carSpace = {
  cardRadius: 18,
  cardPadX: 18,
  cardPadY: 14,
  gap: 9,
} as const;
