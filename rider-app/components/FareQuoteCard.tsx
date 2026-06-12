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
}

export default function FareQuoteCard({ quote, onSelect }: Props) {
  const { colors } = useTheme();
  const styles = createStyles(colors);

  const meta = [
    quote.distance_km != null ? `${quote.distance_km} km` : null,
    quote.duration_minutes != null ? `about ${quote.duration_minutes} min` : null,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <View style={styles.card}>
      <View style={styles.headerRow}>
        <Ionicons name="pricetags-outline" size={17} color={colors.primary} />
        <Text style={styles.headerText}>Your ride options</Text>
      </View>
      {meta ? <Text style={styles.metaText}>{meta}</Text> : null}

      {quote.quotes.map((option, index) => {
        const hasSavings = !!option.promo_savings && option.final_total !== option.total;
        const surge = option.surge_multiplier ?? 1;
        const optionMeta = [
          option.eta_minutes != null ? `${option.eta_minutes} min away` : null,
          option.capacity != null ? `${option.capacity} seats` : null,
        ]
          .filter(Boolean)
          .join(' · ');
        return (
          <TouchableOpacity
            key={option.vehicle_type_id ?? `${option.vehicle_type}-${index}`}
            style={styles.option}
            onPress={() => onSelect(option)}
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
              ) : null}
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
    priceCol: { alignItems: 'flex-end', gap: 1 },
    strikePrice: { fontSize: 12, color: colors.textDim, textDecorationLine: 'line-through' },
    finalPrice: { fontSize: 16, fontWeight: '700', color: colors.text },
    surgeText: { fontSize: 11, fontWeight: '600', color: colors.warning },
    fineprint: { fontSize: 11, color: colors.textDim, textAlign: 'center' },
  });
