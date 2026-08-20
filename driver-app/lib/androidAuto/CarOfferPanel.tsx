/**
 * The ride-offer panel drawn on the Android Auto map surface.
 *
 * ─── Why this exists ─────────────────────────────────────────────────────────
 * The offer moment used to render NOTHING on the surface: the Android Auto
 * alert carried the whole offer as two lines of system-styled text, on the
 * theory that a second panel underneath it would just compete for attention.
 *
 * That reasoning holds for a DUPLICATE panel. It does not hold for this one.
 * The alert is system chrome — we hand it a title string, a subtitle string and
 * two buttons, and Android Auto decides everything else. We cannot make a number
 * big in it, cannot colour it, cannot lay it out. So the single most important
 * thing on the screen — what the ride pays — arrived as ordinary body text in a
 * list of other ordinary body text, while the phone renders it at 52px.
 *
 * The split is therefore: the ALERT owns the decision (Accept / Decline live
 * there and only there), this panel owns the INFORMATION. They are layers, not
 * rivals — which is why nothing here repeats the two button labels, and why the
 * panel is deliberately not touchable.
 *
 * ─── Why it is horizontal ────────────────────────────────────────────────────
 * Head units are wide and SHORT (the test unit is roughly 2.6:1). CarTripCard.tsx
 * documents what happens when you stack rows on one: six of them ate ~70% of the
 * display. So the rich content runs along the axis this screen actually has —
 * three columns, one header strip — instead of down the axis it doesn't.
 *
 * ─── Constraints ─────────────────────────────────────────────────────────────
 * - Google's Car app quality guidelines forbid animation on a connected head
 *   unit, so there is no countdown ring and no progress bar here (the phone has
 *   both). Hierarchy, contrast and type scale carry it instead.
 * - Android Auto forbids in-surface touch, so this holds no Touchable and is
 *   `pointerEvents="none"` throughout, exactly like CarTripCard.
 */
import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import type { TripCard } from './carCard';
import { carColors, carSpace, carType } from './carTheme';

/** A small rounded badge (surge, WAV, quiet, pre-booked, cash). */
function Badge({ label, color }: { label: string; color: string }): React.ReactElement {
  return (
    <View style={[styles.badge, { backgroundColor: color + '26', borderColor: color + '59' }]}>
      <Text style={[styles.badgeText, { color }]} numberOfLines={1}>
        {label}
      </Text>
    </View>
  );
}

/** One metric in the centre stack, e.g. "3.2" + "km". */
function Metric({ value, unit, tint }: { value: string; unit: string; tint?: string }) {
  return (
    <View style={styles.metric}>
      <Text style={[styles.metricValue, tint ? { color: tint } : null]} numberOfLines={1}>
        {value}
      </Text>
      <Text style={[styles.metricUnit, tint ? { color: tint } : null]} numberOfLines={1}>
        {unit}
      </Text>
    </View>
  );
}

/** One end of the trip — coloured dot, caption, address. */
function Stop({
  caption,
  address,
  color,
}: {
  caption: string;
  address: string;
  color: string;
}): React.ReactElement {
  return (
    <View style={styles.stop}>
      <View style={[styles.stopDot, { backgroundColor: color }]} />
      <View style={styles.stopText}>
        <Text style={styles.stopCaption} numberOfLines={1}>
          {caption}
        </Text>
        <Text style={styles.stopAddress} numberOfLines={1}>
          {address}
        </Text>
      </View>
    </View>
  );
}

export function CarOfferPanel({ card }: { card: TripCard }): React.ReactElement {
  // Splitting distance/duration back out of the pre-formatted labels keeps the
  // big number and its unit on separate type scales, the way the phone renders
  // them — "3.2" at metric size with a small "km" reads at a glance where
  // "3.2 km" as one string does not.
  const [distValue, distUnit] = splitUnit(card.distanceLabel);
  const [etaValue, etaUnit] = splitUnit(card.etaLabel);
  const [rateValue, rateUnit] = splitRate(card.perKmLabel);

  const identity = [card.riderName, card.riderRating ? `★${card.riderRating}` : null]
    .filter(Boolean)
    .join(' ');

  return (
    <View style={styles.wrap} pointerEvents="none">
      <View style={styles.panel}>
        {/* Header strip: what this is, who it's for, and the flags that change
            whether a driver wants it at all. */}
        <View style={styles.header}>
          <View style={[styles.liveDot, { backgroundColor: card.accent }]} />
          <Text style={styles.headerTitle} numberOfLines={1}>
            NEW RIDE REQUEST
          </Text>
          {identity.length > 0 && (
            <Text style={styles.headerRider} numberOfLines={1}>
              {identity}
            </Text>
          )}
          <View style={styles.badges}>
            {card.isScheduled && <Badge label="PRE-BOOKED" color={carColors.info} />}
            {card.surgeLabel && <Badge label={`${card.surgeLabel} SURGE`} color={carColors.surge} />}
            {card.wav && <Badge label="WAV" color={carColors.info} />}
            {card.quietMode && <Badge label="QUIET" color={carColors.textMuted} />}
            {card.cashPayment && <Badge label="CASH" color={carColors.gold} />}
          </View>
        </View>

        <View style={styles.body}>
          {/* ── Column 1: the money. The whole reason the panel exists. ── */}
          <View style={styles.moneyCol}>
            <Text style={styles.moneyLabel} numberOfLines={1}>
              YOUR EARNINGS
            </Text>
            <Text style={styles.moneyHero} numberOfLines={1} allowFontScaling={false}>
              {card.totalEarningsLabel ?? '—'}
            </Text>
            {/* Only when a bonus makes the hero differ from the rider's fare —
                otherwise the hero silently disagreeing with the fare reads as a
                bug rather than as extra money. */}
            {card.fareBreakdownLabel && (
              <Text style={styles.moneyBreakdown} numberOfLines={1}>
                {card.fareBreakdownLabel}
              </Text>
            )}
            {/* Spinr's actual differentiator, and absent from this screen until
                now. 0% commission is the reason a driver chose this app; the
                offer is exactly the moment it is worth repeating. */}
            <View style={styles.keepBadge}>
              <Text style={styles.keepText} numberOfLines={1}>
                100% YOURS · $0 COMMISSION
              </Text>
            </View>
          </View>

          {/* ── Column 2: the numbers a driver ranks offers by. ── */}
          <View style={styles.metricsCol}>
            {distValue && <Metric value={distValue} unit={distUnit} />}
            {etaValue && <Metric value={etaValue} unit={etaUnit} />}
            {rateValue && (
              // Tinted when a bonus is lifting the rate — that is the number
              // that makes an otherwise ordinary ride worth taking.
              <Metric
                value={rateValue}
                unit={rateUnit}
                tint={card.fareBreakdownLabel ? carColors.gold : carColors.success}
              />
            )}
          </View>

          {/* ── Column 3: where it actually goes. ── */}
          <View style={styles.routeCol}>
            <Stop
              caption="PICK-UP"
              address={card.pickupLabel ?? 'Pickup location'}
              color={carColors.success}
            />
            <View style={styles.routeLine} />
            <Stop
              caption="DROP-OFF"
              address={card.dropoffLabel ?? 'Drop-off location'}
              color={carColors.brand}
            />
          </View>
        </View>
      </View>
    </View>
  );
}

/** "3.2 km" → ["3.2", "km"]. Null-safe; returns empty strings when absent. */
function splitUnit(label: string | null): [string, string] {
  if (!label) return ['', ''];
  const i = label.indexOf(' ');
  return i === -1 ? [label, ''] : [label.slice(0, i), label.slice(i + 1)];
}

/** "$5.40/km" → ["$5.40", "/km"]. Keeps the slash on the unit. */
function splitRate(label: string | null): [string, string] {
  if (!label) return ['', ''];
  const i = label.indexOf('/');
  return i === -1 ? [label, ''] : [label.slice(0, i), label.slice(i)];
}

const styles = StyleSheet.create({
  wrap: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    padding: 10,
  },
  panel: {
    backgroundColor: carColors.cardBg,
    borderRadius: carSpace.cardRadius,
    borderWidth: 1,
    borderColor: carColors.cardEdge,
    paddingHorizontal: carSpace.cardPadX,
    paddingVertical: 12,
    gap: 10,
    overflow: 'hidden',
  },

  header: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  liveDot: { width: 9, height: 9, borderRadius: 5 },
  headerTitle: {
    color: carColors.text,
    fontSize: carType.micro,
    fontWeight: '800',
    letterSpacing: 1.1,
  },
  headerRider: {
    color: carColors.textMuted,
    fontSize: carType.micro,
    fontWeight: '700',
    flexShrink: 1,
  },
  // Pushed right so the flags sit away from the identity text and read as a
  // separate group rather than a continuation of the rider's name.
  badges: { flexDirection: 'row', gap: 5, marginLeft: 'auto', flexShrink: 0 },
  badge: { borderRadius: 7, borderWidth: 1, paddingHorizontal: 7, paddingVertical: 2 },
  badgeText: { fontSize: carType.micro - 2, fontWeight: '800', letterSpacing: 0.5 },

  body: { flexDirection: 'row', alignItems: 'stretch', gap: 14 },

  moneyCol: { flexShrink: 0, gap: 1 },
  moneyLabel: {
    color: carColors.textDim,
    fontSize: carType.micro - 1,
    fontWeight: '800',
    letterSpacing: 1.1,
  },
  moneyHero: {
    color: carColors.text,
    fontSize: carType.hero,
    fontWeight: '900',
    letterSpacing: -1.5,
    lineHeight: carType.hero + 4,
  },
  moneyBreakdown: { color: carColors.gold, fontSize: carType.micro, fontWeight: '700' },
  keepBadge: {
    alignSelf: 'flex-start',
    backgroundColor: carColors.success + '26',
    borderRadius: 7,
    paddingHorizontal: 8,
    paddingVertical: 3,
    marginTop: 3,
  },
  keepText: {
    color: carColors.success,
    fontSize: carType.micro - 2,
    fontWeight: '800',
    letterSpacing: 0.5,
  },

  metricsCol: {
    justifyContent: 'center',
    gap: 4,
    backgroundColor: carColors.raised,
    borderRadius: 12,
    paddingHorizontal: 12,
    paddingVertical: 8,
    flexShrink: 0,
  },
  metric: { flexDirection: 'row', alignItems: 'baseline', gap: 3 },
  metricValue: { color: carColors.text, fontSize: carType.label + 2, fontWeight: '800' },
  metricUnit: { color: carColors.textDim, fontSize: carType.micro - 1, fontWeight: '600' },

  routeCol: { flex: 1, justifyContent: 'center', minWidth: 0 },
  stop: { flexDirection: 'row', alignItems: 'flex-start', gap: 8 },
  stopDot: { width: 9, height: 9, borderRadius: 5, marginTop: 5 },
  stopText: { flex: 1, minWidth: 0 },
  stopCaption: {
    color: carColors.textDim,
    fontSize: carType.micro - 2,
    fontWeight: '800',
    letterSpacing: 0.8,
  },
  stopAddress: { color: carColors.text, fontSize: carType.label, fontWeight: '600' },
  // Connects the two dots, so the pair reads as one journey rather than two
  // unrelated addresses.
  routeLine: {
    width: 2,
    height: 10,
    backgroundColor: carColors.cardEdge,
    marginLeft: 3.5,
    marginVertical: 2,
  },
});

export default CarOfferPanel;
