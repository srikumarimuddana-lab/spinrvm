/**
 * The Spinr card drawn over the Android Auto map surface.
 *
 * Display-only — Android Auto forbids in-surface touch, so this never holds a
 * touchable; all interaction goes through the template's header actions, map
 * buttons, and the ride-offer alert (see register.ts).
 *
 * ─── Why it looks the way it does ────────────────────────────────────────────
 * Google's Car app quality guidelines forbid animated elements on a connected
 * head unit, and Play enforces it at review before a public release. So none of
 * the sense of quality here can come from motion. It has to come from static
 * properties only: hierarchy, type scale, contrast, spacing, restraint.
 *
 * The organising decision is that MONEY IS THE HERO. On the offer and completed
 * cards the fare is the largest element on the screen — larger than the rider
 * name, larger than the destination — because it is what the driver is deciding
 * on and what the work is for. Previously it sat at 24px in a footer row,
 * visually equal to an ETA. Everything else is deliberately subordinate.
 *
 * Colour comes from carTheme.ts, which sources shared/theme's dark palette —
 * the same tokens the phone renders with, so the head unit reads as the same
 * product rather than a companion built by someone else.
 *
 * Every size here is floored well above its phone equivalent: this is read at
 * arm's length, in a moving vehicle, in one glance.
 */
import React from 'react';
import { Image, StyleSheet, Text, View } from 'react-native';
import type { TripCard } from './carCard';
import { carColors, carSpace, carType } from './carTheme';

const LOGO = require('../../assets/images/spinr-logo.png');

/** Round rider avatar — photo when present, else a coloured initial. */
function Avatar({
  photo,
  name,
  accent,
}: {
  photo: string | null;
  name: string | null;
  accent: string;
}): React.ReactElement {
  if (photo) {
    return <Image source={{ uri: photo }} style={styles.avatar} />;
  }
  const initial = (name?.trim()?.[0] ?? '?').toUpperCase();
  return (
    <View style={[styles.avatar, styles.avatarFallback, { backgroundColor: accent }]}>
      <Text style={styles.avatarInitial}>{initial}</Text>
    </View>
  );
}

/** A small rounded chip (rating, WAV, surge, bonus). */
function Chip({
  label,
  bg,
  color = carColors.text,
}: {
  label: string;
  bg: string;
  color?: string;
}): React.ReactElement {
  return (
    <View style={[styles.chip, { backgroundColor: bg }]}>
      <Text style={[styles.chipText, { color }]} numberOfLines={1}>
        {label}
      </Text>
    </View>
  );
}

export function CarTripCard({ card }: { card: TripCard }): React.ReactElement {
  const showDest = !!card.destinationLabel;
  const meta = [card.etaLabel, card.distanceLabel].filter(Boolean).join(' · ');
  // The two legs where the driver's attention is on money: deciding whether to
  // take a ride, and seeing what one paid. En route, the destination matters
  // more than the fare, so the hero treatment would be misplaced.
  const moneyIsHero = card.leg === 'offer' || card.leg === 'complete';

  return (
    <View style={styles.wrap} pointerEvents="none">
      <View style={styles.card}>
        {/* Hairline top edge. Reads as elevation without a shadow — cheap to
            rasterise on a projected surface, and static. */}
        <View style={styles.edge} />

        {/* Status pill + brand */}
        <View style={styles.headerRow}>
          <View style={[styles.pill, { backgroundColor: card.accent }]}>
            <Text style={styles.pillText} numberOfLines={1}>
              {card.statusLabel}
            </Text>
          </View>
          <Image source={LOGO} style={styles.logo} resizeMode="contain" />
        </View>

        {/* Hero earnings. Micro-label above so the number needs no unit of
            explanation, then the figure at display size. */}
        {moneyIsHero && card.fareLabel && (
          <View style={styles.heroBlock}>
            <Text style={styles.heroLabel}>
              {card.leg === 'complete' ? 'YOU EARNED' : 'YOU’LL EARN'}
            </Text>
            <View style={styles.heroRow}>
              <Text style={styles.hero} numberOfLines={1}>
                {card.fareLabel}
              </Text>
              {card.surgeLabel && <Chip label={card.surgeLabel} bg={carColors.surge} />}
            </View>
            {card.earningsTodayLabel && (
              <Text style={styles.earningsToday} numberOfLines={1}>
                {card.earningsTodayLabel}
              </Text>
            )}
          </View>
        )}

        {/* Rider row — avatar (photo or initial) + name + rating/WAV chips */}
        {(card.riderName || card.riderRating || card.riderPhoto) && (
          <View style={styles.row}>
            <Avatar photo={card.riderPhoto} name={card.riderName} accent={card.accent} />
            {card.riderName && (
              <Text style={styles.rider} numberOfLines={1}>
                {card.riderName}
              </Text>
            )}
            {card.riderRating && (
              <Chip label={`★ ${card.riderRating}`} bg={carColors.raised} color={carColors.gold} />
            )}
            {card.wav && <Chip label="WAV" bg={carColors.info} />}
            {/* Surge already rides with the hero figure when money leads. */}
            {!moneyIsHero && card.surgeLabel && <Chip label={card.surgeLabel} bg={carColors.surge} />}
          </View>
        )}

        {/* Earnings perks — bonus + active incentive/quest. Gold, because this
            is the "you made extra" signal and should feel distinct from fare. */}
        {(card.bonusLabel || card.perkLabel) && (
          <View style={styles.row}>
            {card.bonusLabel && <Chip label={card.bonusLabel} bg={carColors.gold} color="#1A1A1A" />}
            {card.perkLabel && (
              <Text style={styles.perk} numberOfLines={1}>
                {card.perkLabel}
              </Text>
            )}
          </View>
        )}

        {/* Destination row */}
        {showDest && (
          <View style={styles.destRow}>
            <View style={[styles.dot, { backgroundColor: card.accent }]} />
            <View style={styles.destText}>
              {card.destinationCaption && (
                <Text style={styles.caption} numberOfLines={1}>
                  {card.destinationCaption}
                </Text>
              )}
              <Text style={styles.dest} numberOfLines={1}>
                {card.destinationLabel}
              </Text>
            </View>
          </View>
        )}

        {/* En-route footer: fare stays present but subordinate to the destination. */}
        {!moneyIsHero && (card.fareLabel || meta) && (
          <View style={styles.footerRow}>
            {card.fareLabel && <Text style={styles.fare}>{card.fareLabel}</Text>}
            {meta.length > 0 && (
              <Text style={styles.meta} numberOfLines={1}>
                {meta}
              </Text>
            )}
          </View>
        )}
        {/* When money leads, ETA/distance still belong — just quietly. */}
        {moneyIsHero && meta.length > 0 && (
          <Text style={styles.meta} numberOfLines={1}>
            {meta}
          </Text>
        )}

        {/* Guidance hint (e.g. phone-only start-trip prompt) */}
        {card.hint && (
          <Text style={styles.hint} numberOfLines={2}>
            {card.hint}
          </Text>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    padding: 12,
  },
  card: {
    backgroundColor: carColors.cardBg,
    borderRadius: carSpace.cardRadius,
    paddingHorizontal: carSpace.cardPadX,
    paddingVertical: carSpace.cardPadY,
    gap: carSpace.gap,
    overflow: 'hidden',
  },
  edge: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    height: 1,
    backgroundColor: carColors.cardEdge,
  },
  headerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  pill: {
    borderRadius: 999,
    paddingHorizontal: 13,
    paddingVertical: 6,
    maxWidth: '78%',
  },
  pillText: { color: carColors.text, fontSize: carType.label, fontWeight: '700', letterSpacing: 0.2 },
  logo: { width: 60, height: 21, opacity: 0.92 },

  heroBlock: { gap: 2 },
  heroLabel: {
    color: carColors.textMuted,
    fontSize: carType.micro,
    fontWeight: '700',
    letterSpacing: 1.1,
  },
  heroRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  hero: {
    color: carColors.text,
    fontSize: carType.hero,
    fontWeight: '800',
    letterSpacing: -1,
  },
  earningsToday: { color: carColors.textDim, fontSize: carType.label, fontWeight: '600' },

  row: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  avatar: { width: 42, height: 42, borderRadius: 21, backgroundColor: carColors.raised },
  avatarFallback: { alignItems: 'center', justifyContent: 'center' },
  avatarInitial: { color: carColors.text, fontSize: 21, fontWeight: '800' },
  rider: { color: carColors.text, fontSize: carType.title, fontWeight: '700', flexShrink: 1 },
  perk: { color: carColors.success, fontSize: carType.label, fontWeight: '600', flexShrink: 1 },
  chip: { borderRadius: 9, paddingHorizontal: 9, paddingVertical: 4 },
  chipText: { fontSize: carType.micro, fontWeight: '700' },

  destRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  dot: { width: 12, height: 12, borderRadius: 6 },
  destText: { flexShrink: 1 },
  caption: {
    color: carColors.textMuted,
    fontSize: carType.micro,
    fontWeight: '700',
    letterSpacing: 0.6,
    textTransform: 'uppercase',
  },
  dest: { color: carColors.text, fontSize: carType.body, fontWeight: '600' },

  footerRow: { flexDirection: 'row', alignItems: 'baseline', gap: 12 },
  fare: { color: carColors.text, fontSize: carType.title, fontWeight: '800' },
  meta: { color: carColors.textDim, fontSize: carType.label, fontWeight: '600' },
  hint: { color: carColors.gold, fontSize: carType.label, fontWeight: '600' },
});
