import React, { forwardRef } from 'react';
import { StyleSheet, Text as RNText, TextProps as RNTextProps, TextStyle } from 'react-native';

/**
 * Text — themed drop-in replacement for React Native's `Text`.
 *
 * ACTION_ITEMS.md UX1: rider-app/driver-app load all 4 Plus Jakarta Sans
 * weights at boot (rider-app/app/_layout.tsx's `useFonts()` call) but had no
 * themed `Text` default, so the brand font only applied where a screen
 * explicitly set `fontFamily` — everywhere else `Text` silently rendered the
 * OS system font. This component closes that gap by defaulting `fontFamily`
 * app-wide, so a *new* screen can no longer ship off-brand-font by omission
 * (the actual acceptance bar for UX1 — see the item for full context).
 *
 * Usage: import { Text } from '@shared/components/Text' in place of
 * react-native's `Text`. Same props, same behavior otherwise — this only
 * adds a default `fontFamily`.
 *
 * Weight → family mapping: the 4 loaded weights are PlusJakartaSans_400Regular
 * /500Medium/600SemiBold/700Bold. `style`'s `fontWeight` (if any) is read and
 * mapped to the matching family:
 *   '400' / 'normal'        → Regular
 *   '500'                   → Medium
 *   '600'                   → SemiBold
 *   '700' / 'bold'          → Bold
 *   any other numeric weight (e.g. '300', '800', '900') → snapped to the
 *     nearest of the 4 loaded weights, since Plus Jakarta Sans isn't loaded
 *     at those steps and a custom-font family with no matching file would
 *     otherwise silently fall back to the system font again.
 *   no fontWeight at all    → Regular (400)
 *
 * That last default (no `fontWeight` set) is deliberately Regular, not
 * whatever's statistically "typical" — React Native's own default for
 * unstyled Text is normal/400 weight, so defaulting to Regular here changes
 * only the font *family* for plain body copy, not its visual weight
 * (matches this file's "additive theming, not a new behavior" scope).
 * Grepping rider-app's own fontWeight-only Text usages (no adjacent
 * fontFamily) for context: the explicit values skew bold/semibold (121x
 * '700', 92x '600', 33x '800', 23x '500', 8x 'bold', 2x '900') — but nobody
 * writes `fontWeight: '400'` explicitly since that's already the implicit
 * default, so that count is about the *explicit*-weight mapping above
 * (already exact for 400/500/600/700/bold), not about this no-weight
 * fallback.
 *
 * An explicit `fontFamily` in `style` always wins — this component's default
 * is applied underneath the caller's style, not over it, so a genuine
 * edge case (e.g. a monospace fare digit, an icon-font glyph) can still
 * opt out.
 */

const FAMILY_BY_WEIGHT_STEP: Record<400 | 500 | 600 | 700, string> = {
  400: 'PlusJakartaSans_400Regular',
  500: 'PlusJakartaSans_500Medium',
  600: 'PlusJakartaSans_600SemiBold',
  700: 'PlusJakartaSans_700Bold',
};

const WEIGHT_STEPS = [400, 500, 600, 700] as const;

const DEFAULT_FAMILY = FAMILY_BY_WEIGHT_STEP[400];

// RN's fontWeight type also allows a handful of iOS-only keyword weights
// (ultralight/thin/.../black) and plain numbers, in addition to the numeric
// strings and 'normal'/'bold' rider-app's own styles actually use (verified
// via grep — none of these keywords appear in this codebase today). Handled
// here for type-correctness and so a future style using one degrades to the
// nearest loaded family instead of silently falling through to the default.
const KEYWORD_WEIGHTS: Record<string, number> = {
  ultralight: 100,
  thin: 100,
  light: 300,
  regular: 400,
  normal: 400,
  medium: 500,
  semibold: 600,
  condensed: 400,
  condensedBold: 700,
  heavy: 800,
  black: 900,
  bold: 700,
};

function familyForWeight(fontWeight: TextStyle['fontWeight']): string {
  if (fontWeight == null) return DEFAULT_FAMILY;

  let numeric: number;
  if (typeof fontWeight === 'number') {
    numeric = fontWeight;
  } else if (fontWeight in KEYWORD_WEIGHTS) {
    numeric = KEYWORD_WEIGHTS[fontWeight];
  } else {
    numeric = parseInt(fontWeight, 10);
  }
  if (Number.isNaN(numeric)) return DEFAULT_FAMILY;

  let nearestStep: (typeof WEIGHT_STEPS)[number] = WEIGHT_STEPS[0];
  let nearestDistance = Math.abs(WEIGHT_STEPS[0] - numeric);
  for (const step of WEIGHT_STEPS) {
    const distance = Math.abs(step - numeric);
    if (distance < nearestDistance) {
      nearestStep = step;
      nearestDistance = distance;
    }
  }
  return FAMILY_BY_WEIGHT_STEP[nearestStep];
}

export type TextProps = RNTextProps;

export const Text = forwardRef<RNText, TextProps>(function Text({ style, ...rest }, ref) {
  const flattened = StyleSheet.flatten(style) as TextStyle | undefined;
  const family = familyForWeight(flattened?.fontWeight);

  return <RNText ref={ref} style={[{ fontFamily: family }, style]} {...rest} />;
});

export default Text;
