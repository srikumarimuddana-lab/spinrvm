/**
 * In-chat fare quote card.
 *
 * Renders a fare_quote action from the AI: exact totals per available
 * vehicle option (same estimate engine as the booking screen) with the
 * best eligible promo pre-applied. Tapping an option asks the assistant
 * to book it — the binding confirmation still happens on the booking
 * proposal card.
 */
import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import type { AiAction, FareQuoteOption } from '@shared/types/ai';

type FareQuoteAction = Extract<AiAction, { type: 'fare_quote' }>;

interface Props {
  quote: FareQuoteAction;
  onSelect: (option: FareQuoteOption) => void;
  /** ACTION_ITEMS.md AI8: true once a newer conversation turn has started —
   * dims the card and disables its options so a stale-but-still-consistent
   * quote can't be tapped and booked at a possibly different price. */
  disabled?: boolean;
}

/** Line-item fare details for the recommended (cheapest) option, plus its
 * promo savings — so the rider sees exactly what they'd pay before booking. */
function FareBreakdown({
  quote,
  styles,
}: {
  quote: FareQuoteAction;
  styles: ReturnType<typeof createStyles>;
}) {
  const option =
    quote.quotes.find((q) => q.vehicle_type_id === quote.recommended_vehicle_type_id) ?? quote.quotes[0];
  if (!option?.breakdown?.length) return null;

  return (
    <View style={styles.breakdownWrap}>
      <Text style={styles.breakdownTitle}>Fare details — {option.vehicle_type ?? 'recommended'}</Text>
      {option.breakdown.map((line, i) => (
        <View key={`${line.label}-${i}`} style={styles.breakdownRow}>
          <Text style={styles.breakdownLabel}>{line.label}</Text>
          {line.amount != null ? <Text style={styles.breakdownAmount}>${line.amount.toFixed(2)}</Text> : null}
        </View>
      ))}
      {option.promo_savings ? (
        <View style={styles.breakdownRow}>
          <Text style={styles.breakdownSavings}>Promo {option.promo_code}</Text>
          <Text style={styles.breakdownSavings}>-${option.promo_savings}</Text>
        </View>
      ) : null}
      <View style={styles.breakdownRow}>
        <Text style={styles.breakdownTotal}>You pay</Text>
        <Text style={styles.breakdownTotal}>${option.final_total}</Text>
      </View>
    </View>
  );
}

export default function FareQuoteCard({ quote, onSelect, disabled }: Props) {
  const { colors } = useTheme();
  const styles = createStyles(colors);

  const meta = [
    quote.distance_km != null ? `${quote.distance_km} km` : null,
    quote.duration_minutes != null ? `about ${quote.duration_minutes} min` : null,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <View style={[styles.card, disabled && styles.staleCard]}>
      <View style={styles.headerRow}>
        <Ionicons name="pricetags-outline" size={17} color={colors.primary} />
        <Text style={styles.headerText}>Your ride options</Text>
      </View>
      {meta ? <Text style={styles.metaText}>{meta}</Text> : null}
      {disabled ? (
        <Text style={styles.staleCardNote}>The conversation has moved on — ask again if you still need this.</Text>
      ) : null}

      {quote.quotes.map((option, index) => {
        const hasSavings = !!option.promo_savings && option.final_total !== option.total;
        const surge = option.surge_multiplier ?? 1;
        const optionMeta = [
          option.eta_minutes != null
            ? `${option.eta_minutes} min${option.closest_driver_km != null ? ` (${option.closest_driver_km} km)` : ''} away`
            : null,
          option.capacity != null ? `${option.capacity} seats` : null,
        ]
          .filter(Boolean)
          .join(' · ');
        return (
          <TouchableOpacity
            key={option.vehicle_type_id ?? `${option.vehicle_type}-${index}`}
            style={styles.option}
            onPress={() => onSelect(option)}
            disabled={disabled}
            accessibilityRole="button"
            accessibilityLabel={`Book ${option.vehicle_type ?? 'this option'} for $${option.final_total}`}
          >
            <View style={styles.optionCopy}>
              <Text style={styles.vehicleName}>{option.vehicle_type ?? 'Ride'}</Text>
              {optionMeta ? <Text style={styles.optionMeta}>{optionMeta}</Text> : null}
              {hasSavings ? (
                <View style={styles.savingsPill}>
                  <Ionicons name="pricetag-outline" size={11} color={colors.success} />
                  <Text style={styles.savingsText}>
                    {option.promo_code} · save ${option.promo_savings}
                  </Text>
                </View>
              ) : (
                <Text style={styles.noPromoText}>No promo applied</Text>
              )}
            </View>
            <View style={styles.priceCol}>
              {hasSavings ? <Text style={styles.strikePrice}>${option.total}</Text> : null}
              <Text style={styles.finalPrice}>${option.final_total}</Text>
              {surge > 1 ? (
                <Text style={styles.surgeText}>{surge}x surge</Text>
              ) : null}
            </View>
          </TouchableOpacity>
        );
      })}

      <FareBreakdown quote={quote} styles={styles} />

      <Text style={styles.fineprint}>Totals include taxes & fees. Tap an option to book it.</Text>
    </View>
  );
}

const createStyles = (colors: ThemeColors) =>
  StyleSheet.create({
    card: {
      marginLeft: 34,
      padding: 14,
      borderRadius: 14,
      backgroundColor: colors.surface,
      borderWidth: 1,
      borderColor: colors.border,
      gap: 8,
      alignSelf: 'stretch',
    },
    headerRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    headerText: { fontSize: 15, fontWeight: '700', color: colors.text },
    metaText: { fontSize: 12, color: colors.textDim },
    staleCard: { opacity: 0.45 },
    staleCardNote: { fontSize: 12, color: colors.textDim, fontStyle: 'italic' },
    option: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 10,
      paddingVertical: 10,
      paddingHorizontal: 10,
      borderRadius: 10,
      backgroundColor: colors.surfaceLight,
    },
    optionCopy: { flex: 1, gap: 3 },
    vehicleName: { fontSize: 14, fontWeight: '700', color: colors.text },
    optionMeta: { fontSize: 12, color: colors.textDim },
    savingsPill: { flexDirection: 'row', alignItems: 'center', gap: 4, marginTop: 2 },
    savingsText: { fontSize: 11, fontWeight: '600', color: colors.success },
    noPromoText: { fontSize: 11, color: colors.textDim, marginTop: 2 },
    breakdownWrap: {
      gap: 4,
      paddingTop: 8,
      borderTopWidth: StyleSheet.hairlineWidth,
      borderTopColor: colors.border,
    },
    breakdownTitle: { fontSize: 12, fontWeight: '700', color: colors.text },
    breakdownRow: { flexDirection: 'row', justifyContent: 'space-between', gap: 8 },
    breakdownLabel: { flex: 1, fontSize: 12, color: colors.textDim },
    breakdownAmount: { fontSize: 12, color: colors.text },
    breakdownSavings: { fontSize: 12, fontWeight: '600', color: colors.success },
    breakdownTotal: { fontSize: 13, fontWeight: '700', color: colors.text },
    priceCol: { alignItems: 'flex-end', gap: 1 },
    strikePrice: { fontSize: 12, color: colors.textDim, textDecorationLine: 'line-through' },
    finalPrice: { fontSize: 16, fontWeight: '700', color: colors.text },
    surgeText: { fontSize: 11, fontWeight: '600', color: colors.warning },
    fineprint: { fontSize: 11, color: colors.textDim, textAlign: 'center' },
  });
