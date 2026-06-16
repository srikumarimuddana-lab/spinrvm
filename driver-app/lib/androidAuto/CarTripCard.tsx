/**
 * The branded Lyft-style card drawn over the Android Auto map surface.
 *
 * Display-only — Android Auto forbids in-surface touch, so this never holds a
 * touchable; all interaction goes through the template's header actions, map
 * buttons, and the ride-offer alert (see register.ts). It just renders the
 * glanceable card model from carCard.ts: a coloured status pill, the rider, the
 * current destination with ETA/distance, the fare with a surge badge, a WAV
 * chip, and an optional guidance hint.
 *
 * Big type, high contrast, and a single card — sized for a dashboard read at a
 * glance, per Android Auto's driver-distraction guidance.
 */
import React from 'react';
import { Image, StyleSheet, Text, View } from 'react-native';
import type { TripCard } from './carCard';

const CARD_BG = 'rgba(20,20,22,0.92)';
const ON_DARK = '#ffffff';
const MUTED = '#b8b8bf';
const SURGE_BG = '#ff8a00';

const LOGO = require('../../assets/images/spinr-logo.png');

/** A small rounded chip (rating, WAV, surge). */
function Chip({
  label,
  bg,
  color = ON_DARK,
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

  return (
    <View style={styles.wrap} pointerEvents="none">
      <View style={styles.card}>
        {/* Status pill + brand */}
        <View style={styles.headerRow}>
          <View style={[styles.pill, { backgroundColor: card.accent }]}>
            <Text style={styles.pillText} numberOfLines={1}>
              {card.statusLabel}
            </Text>
          </View>
          <Image source={LOGO} style={styles.logo} resizeMode="contain" />
        </View>

        {/* Rider row */}
        {(card.riderName || card.riderRating) && (
          <View style={styles.row}>
            {card.riderName && (
              <Text style={styles.rider} numberOfLines={1}>
                {card.riderName}
              </Text>
            )}
            {card.riderRating && <Chip label={`★ ${card.riderRating}`} bg="#2c2c2e" color="#ffd23f" />}
            {card.wav && <Chip label="WAV" bg="#0a84ff" />}
            {card.surgeLabel && <Chip label={card.surgeLabel} bg={SURGE_BG} />}
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

        {/* Fare + ETA/distance footer */}
        {(card.fareLabel || meta) && (
          <View style={styles.footerRow}>
            {card.fareLabel && <Text style={styles.fare}>{card.fareLabel}</Text>}
            {meta.length > 0 && (
              <Text style={styles.meta} numberOfLines={1}>
                {meta}
              </Text>
            )}
          </View>
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
    backgroundColor: CARD_BG,
    borderRadius: 16,
    paddingHorizontal: 16,
    paddingVertical: 12,
    gap: 8,
  },
  headerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  pill: {
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 5,
    maxWidth: '78%',
  },
  pillText: { color: ON_DARK, fontSize: 16, fontWeight: '700' },
  logo: { width: 56, height: 20, opacity: 0.9 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  rider: { color: ON_DARK, fontSize: 22, fontWeight: '700', flexShrink: 1 },
  chip: { borderRadius: 8, paddingHorizontal: 8, paddingVertical: 3 },
  chipText: { fontSize: 13, fontWeight: '700' },
  destRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  dot: { width: 12, height: 12, borderRadius: 6 },
  destText: { flexShrink: 1 },
  caption: { color: MUTED, fontSize: 12, fontWeight: '600', textTransform: 'uppercase' },
  dest: { color: ON_DARK, fontSize: 18, fontWeight: '600' },
  footerRow: { flexDirection: 'row', alignItems: 'baseline', gap: 12 },
  fare: { color: ON_DARK, fontSize: 24, fontWeight: '800' },
  meta: { color: MUTED, fontSize: 15, fontWeight: '600' },
  hint: { color: '#ffd23f', fontSize: 14, fontWeight: '600' },
});
