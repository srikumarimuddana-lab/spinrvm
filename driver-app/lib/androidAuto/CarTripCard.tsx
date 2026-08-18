/**
 * The Spinr status bar drawn over the Android Auto map surface.
 *
 * Display-only — Android Auto forbids in-surface touch, so this never holds a
 * touchable; all interaction goes through the template's header actions, map
 * buttons, and the ride-offer alert (see register.ts).
 *
 * ─── Why it is a BAR and not a card ──────────────────────────────────────────
 * It used to be a full-width stacked sheet: status pill, brand logo, hero fare,
 * avatar row, chips, destination, footer. On a phone that reads well. On a head
 * unit it does not — those screens are wide and SHORT (the test unit is roughly
 * 2.6:1), so six stacked rows consumed about 70% of the display and squeezed the
 * map, the one thing a driver actually needs, into a letterbox strip.
 *
 * So: one row, ~10% of the height, full width — which is the axis a wide screen
 * has to spare. The map keeps its height, which is what you need to see the road
 * ahead along a route.
 *
 * What was removed and why:
 *   - status pill    already rendered top-left by carSurface; it was duplicated
 *   - Spinr logo     invisible against the dark card, and pure decoration on a
 *                    surface where every pixel is competing with the map
 *   - rider avatar   a 42px circle is a lot of height to spend on decoration
 *   - hero fare      en route, the destination matters more than the number;
 *                    the fare is still present, just not 46px tall
 *
 * WAV and surge survive as chips. WAV is a Saskatchewan accessibility
 * obligation, and surge changes what the driver is paid — neither is decoration.
 *
 * Nothing is shown at all during an offer: the Android Auto alert already
 * carries rider, fare, bonus, ETA and the Accept/Decline buttons, and drawing
 * this underneath it produced two overlapping panels with no clear target.
 * See carSurface.tsx.
 */
import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import type { TripCard } from './carCard';
import { carColors, carSpace, carType } from './carTheme';

/** A small rounded chip (WAV, surge). */
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
    <View style={styles.chip}>
      <Text style={[styles.chipText, { backgroundColor: bg, color }]} numberOfLines={1}>
        {label}
      </Text>
    </View>
  );
}

export function CarTripCard({ card }: { card: TripCard }): React.ReactElement {
  const meta = [card.etaLabel, card.distanceLabel].filter(Boolean).join(' · ');
  // The one leg where the number genuinely leads: the trip is over and what it
  // paid is the whole message. Everywhere else the destination leads.
  const moneyLeads = card.leg === 'complete';
  // Caption line doubles as the rider identifier so the name costs no extra row.
  const caption = [card.destinationCaption, card.riderName].filter(Boolean).join(' · ');

  return (
    <View style={styles.wrap} pointerEvents="none">
      <View style={styles.bar}>
        {/* Hairline top edge. Reads as elevation without a shadow — cheap to
            rasterise on a projected surface, and static (Car app quality
            guidelines forbid animation on a connected head unit). */}
        <View style={styles.edge} />

        <View style={styles.row}>
          <View style={[styles.dot, { backgroundColor: card.accent }]} />

          <View style={styles.textCol}>
            {caption.length > 0 && (
              <View style={styles.captionRow}>
                <Text style={styles.caption} numberOfLines={1}>
                  {caption}
                </Text>
                {card.wav && <Chip label="WAV" bg={carColors.info} />}
                {card.surgeLabel && <Chip label={card.surgeLabel} bg={carColors.surge} />}
              </View>
            )}
            <Text style={styles.dest} numberOfLines={1}>
              {card.destinationLabel ?? card.statusLabel}
            </Text>
          </View>

          <View style={styles.moneyCol}>
            {card.fareLabel && (
              <Text style={moneyLeads ? styles.fareLead : styles.fare} numberOfLines={1}>
                {card.fareLabel}
              </Text>
            )}
            {meta.length > 0 && (
              <Text style={styles.meta} numberOfLines={1}>
                {meta}
              </Text>
            )}
          </View>
        </View>

        {/* Guidance hint (e.g. the phone-only PIN prompt at pickup). Its own
            line only when there is one — at `arrived_at_pickup` it is the most
            important thing on the screen, and everywhere else it costs nothing. */}
        {card.hint && (
          <Text style={styles.hint} numberOfLines={1}>
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
    padding: 10,
  },
  bar: {
    backgroundColor: carColors.cardBg,
    borderRadius: carSpace.cardRadius,
    // Bordered on ALL sides, not just the top edge. The bar is near-black, and
    // Android Auto switches the map to a dark style at night — without an
    // outline it dissolves into its own background exactly when a driver is
    // most reliant on glanceability.
    borderWidth: 1,
    borderColor: carColors.cardEdge,
    paddingHorizontal: carSpace.cardPadX,
    // Tight on purpose. Every vertical pixel here is one the map does not get,
    // and the map is what the driver is looking at.
    paddingVertical: 10,
    gap: 4,
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
  row: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  dot: { width: 10, height: 10, borderRadius: 5 },
  textCol: { flex: 1, gap: 1 },
  captionRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  caption: {
    color: carColors.textMuted,
    fontSize: carType.micro,
    fontWeight: '700',
    letterSpacing: 0.6,
    textTransform: 'uppercase',
    flexShrink: 1,
  },
  dest: { color: carColors.text, fontSize: carType.body, fontWeight: '600' },

  moneyCol: { alignItems: 'flex-end' },
  fare: { color: carColors.text, fontSize: carType.title, fontWeight: '800' },
  // Completed trips get one size up — still nothing like the old 46px hero.
  fareLead: { color: carColors.gold, fontSize: carType.title + 6, fontWeight: '800' },
  meta: { color: carColors.textDim, fontSize: carType.micro, fontWeight: '600' },

  chip: { borderRadius: 7, overflow: 'hidden' },
  chipText: {
    fontSize: carType.micro - 1,
    fontWeight: '800',
    paddingHorizontal: 7,
    paddingVertical: 2,
  },
  hint: { color: carColors.gold, fontSize: carType.micro, fontWeight: '700' },
});
