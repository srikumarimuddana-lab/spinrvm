import React, { useEffect, useState, useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  ActivityIndicator,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useAuthStore } from '@shared/store/authStore';
import api from '@shared/api/client';

/** Parse a string-encoded Decimal or number to a displayable "$X.XX" string.
 *  Returns "$0.00" for null/undefined/NaN — never crashes on missing fields.
 */
const fmt = (value: string | number | null | undefined): string => {
  if (value === null || value === undefined) return '$0.00';
  const n = parseFloat(String(value));
  if (isNaN(n)) return '$0.00';
  return `$${n.toFixed(2)}`;
};

/** Format an ISO date string for display. */
const fmtDate = (iso: string | null | undefined): string => {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('en-CA', {
      weekday: 'short',
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return '—';
  }
};

interface RideDetail {
  id: string;
  ride_code?: string;
  pickup_address?: string;
  dropoff_address?: string;
  ride_completed_at?: string;
  created_at?: string;
  grand_total?: string | number;
  base_fare?: string | number;
  distance_fare?: string | number;
  time_fare?: string | number;
  booking_fee?: string | number;
  surge_multiplier?: string | number;
  distance_km?: string | number;
  duration_minutes?: string | number;
  driver?: {
    name?: string;
  };
}

export default function ReceiptScreen() {
  const router = useRouter();
  const { rideId } = useLocalSearchParams<{ rideId: string }>();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const user = useAuthStore((s) => s.user);

  const [ride, setRide] = useState<RideDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (!rideId) return;
    let cancelled = false;
    const load = async () => {
      try {
        setIsLoading(true);
        setLoadError(null);
        const response = await api.get(`/rides/${rideId}`);
        if (!cancelled) setRide(response.data);
      } catch (err: any) {
        if (!cancelled) setLoadError('Could not load receipt. Please try again.');
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };
    load();
    return () => { cancelled = true; };
  }, [rideId]);

  // ── Fare calculations ────────────────────────────────────────────
  // grand_total from the API already includes GST + PST. Approximate
  // the pre-tax subtotal as grand_total / 1.11 (since 1.05 * 1.06 = 1.111).
  // Then derive GST (5%) and PST (6%) from that subtotal.
  const grandTotal = parseFloat(String(ride?.grand_total ?? 0)) || 0;
  const subtotal = grandTotal / 1.11;
  const gst = subtotal * 0.05;
  const pst = subtotal * 0.06;

  const surgeMultiplier = parseFloat(String(ride?.surge_multiplier ?? 1)) || 1;
  const showSurge = surgeMultiplier > 1.0;

  const distanceFare = parseFloat(String(ride?.distance_fare ?? 0)) || 0;
  const timeFare = parseFloat(String(ride?.time_fare ?? 0)) || 0;

  const hasEmail = Boolean(user?.email);

  if (isLoading) {
    return (
      <SafeAreaView style={styles.container} edges={['top']}>
        <View style={styles.header}>
          <TouchableOpacity style={styles.backButton} onPress={() => router.back()}>
            <Ionicons name="arrow-back" size={24} color={colors.text} />
          </TouchableOpacity>
          <Text style={styles.headerTitle}>Ride Receipt</Text>
          <View style={{ width: 44 }} />
        </View>
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
      </SafeAreaView>
    );
  }

  if (loadError || !ride) {
    return (
      <SafeAreaView style={styles.container} edges={['top']}>
        <View style={styles.header}>
          <TouchableOpacity style={styles.backButton} onPress={() => router.back()}>
            <Ionicons name="arrow-back" size={24} color={colors.text} />
          </TouchableOpacity>
          <Text style={styles.headerTitle}>Ride Receipt</Text>
          <View style={{ width: 44 }} />
        </View>
        <View style={styles.errorContainer}>
          <Ionicons name="alert-circle-outline" size={48} color={colors.textDim} />
          <Text style={styles.errorText}>{loadError ?? 'Receipt not found.'}</Text>
        </View>
      </SafeAreaView>
    );
  }

  const rideLabel = ride.ride_code || ride.id?.slice(0, 8).toUpperCase() || '—';
  const completedAt = fmtDate(ride.ride_completed_at ?? ride.created_at);
  const driverName = ride.driver?.name ?? '—';
  const distanceKm = parseFloat(String(ride.distance_km ?? 0)) || 0;
  const durationMin = parseFloat(String(ride.duration_minutes ?? 0)) || 0;

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity
          style={styles.backButton}
          onPress={() => router.back()}
          accessibilityRole="button"
          accessibilityLabel="Go back"
        >
          <Ionicons name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Ride Receipt</Text>
        <View style={{ width: 44 }} />
      </View>

      <ScrollView
        contentContainerStyle={styles.scrollContent}
        showsVerticalScrollIndicator={false}
      >
        {/* Trip Summary Card */}
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Trip Summary</Text>

          <View style={styles.routeRow}>
            <View style={styles.routeDots}>
              <View style={styles.dotPickup} />
              <View style={styles.routeLine} />
              <View style={styles.dotDropoff} />
            </View>
            <View style={styles.routeAddresses}>
              <Text style={styles.routeAddress} numberOfLines={2}>
                {ride.pickup_address ?? '—'}
              </Text>
              <View style={{ height: 8 }} />
              <Text style={styles.routeAddress} numberOfLines={2}>
                {ride.dropoff_address ?? '—'}
              </Text>
            </View>
          </View>

          <View style={styles.metaRow}>
            <Ionicons name="calendar-outline" size={14} color={colors.textDim} />
            <Text style={styles.metaText}>{completedAt}</Text>
          </View>

          <View style={styles.metaRow}>
            <Ionicons name="person-outline" size={14} color={colors.textDim} />
            <Text style={styles.metaText}>Driver: {driverName}</Text>
          </View>

          {distanceKm > 0 && (
            <View style={styles.metaRow}>
              <Ionicons name="speedometer-outline" size={14} color={colors.textDim} />
              <Text style={styles.metaText}>{distanceKm.toFixed(1)} km</Text>
            </View>
          )}

          {durationMin > 0 && (
            <View style={styles.metaRow}>
              <Ionicons name="time-outline" size={14} color={colors.textDim} />
              <Text style={styles.metaText}>{Math.round(durationMin)} min</Text>
            </View>
          )}

          <View style={styles.rideCodeRow}>
            <Text style={styles.rideCodeLabel}>Ride</Text>
            <Text style={styles.rideCodeValue}>{rideLabel}</Text>
          </View>
        </View>

        {/* Fare Breakdown Card */}
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Fare Breakdown</Text>

          <View style={styles.fareTable}>
            <LineItem label="Base fare" value={fmt(ride.base_fare)} styles={styles} colors={colors} />

            {distanceFare > 0 && (
              <LineItem label="Distance charge" value={fmt(ride.distance_fare)} styles={styles} colors={colors} />
            )}

            {timeFare > 0 && (
              <LineItem label="Time charge" value={fmt(ride.time_fare)} styles={styles} colors={colors} />
            )}

            <LineItem label="Booking fee" value={fmt(ride.booking_fee)} styles={styles} colors={colors} />

            {showSurge && (
              <LineItem
                label={`Surge (${surgeMultiplier.toFixed(1)}x)`}
                value=""
                styles={styles}
                colors={colors}
                isSurge
              />
            )}

            <View style={styles.divider} />

            <LineItem label="Subtotal" value={`$${subtotal.toFixed(2)}`} styles={styles} colors={colors} />
            <LineItem label="GST (5%)" value={`$${gst.toFixed(2)}`} styles={styles} colors={colors} />
            <LineItem label="PST (6%)" value={`$${pst.toFixed(2)}`} styles={styles} colors={colors} />

            <View style={styles.divider} />

            <View style={styles.totalRow}>
              <Text style={styles.totalLabel}>Total</Text>
              <Text style={styles.totalValue}>{fmt(ride.grand_total)}</Text>
            </View>
          </View>
        </View>

        {/* Email Note */}
        {hasEmail && (
          <View style={styles.emailNote}>
            <Ionicons name="mail-outline" size={16} color={colors.textDim} />
            <Text style={styles.emailNoteText}>Receipt emailed to your address on file.</Text>
          </View>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

interface LineItemProps {
  label: string;
  value: string;
  styles: ReturnType<typeof createStyles>;
  colors: ThemeColors;
  isSurge?: boolean;
}

const LineItem = ({ label, value, styles, isSurge }: LineItemProps) => (
  <View style={styles.lineRow}>
    <Text style={styles.lineLabel}>{label}</Text>
    <Text style={styles.lineValue}>{isSurge ? '—' : value}</Text>
  </View>
);

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
      flex: 1,
      backgroundColor: colors.background,
    },
    header: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: 16,
      paddingVertical: 12,
      backgroundColor: colors.surface,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
    },
    backButton: {
      width: 44,
      height: 44,
      justifyContent: 'center',
      alignItems: 'center',
    },
    headerTitle: {
      fontSize: 18,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
    },
    loadingContainer: {
      flex: 1,
      justifyContent: 'center',
      alignItems: 'center',
    },
    errorContainer: {
      flex: 1,
      justifyContent: 'center',
      alignItems: 'center',
      paddingHorizontal: 32,
      gap: 12,
    },
    errorText: {
      fontSize: 15,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
      textAlign: 'center',
    },
    scrollContent: {
      padding: 16,
      paddingBottom: 32,
      gap: 16,
    },
    card: {
      backgroundColor: colors.surface,
      borderRadius: 20,
      padding: 20,
      borderWidth: 1,
      borderColor: colors.border,
    },
    cardTitle: {
      fontSize: 16,
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text,
      marginBottom: 16,
    },
    // Route display
    routeRow: {
      flexDirection: 'row',
      marginBottom: 12,
    },
    routeDots: {
      width: 20,
      alignItems: 'center',
      paddingTop: 4,
    },
    dotPickup: {
      width: 10,
      height: 10,
      borderRadius: 5,
      backgroundColor: colors.primary,
    },
    routeLine: {
      width: 2,
      flex: 1,
      backgroundColor: colors.border,
      marginVertical: 3,
      minHeight: 20,
    },
    dotDropoff: {
      width: 10,
      height: 10,
      borderRadius: 5,
      backgroundColor: colors.text,
    },
    routeAddresses: {
      flex: 1,
      paddingLeft: 10,
    },
    routeAddress: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.text,
      lineHeight: 20,
    },
    metaRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
      marginBottom: 6,
    },
    metaText: {
      fontSize: 13,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
    },
    rideCodeRow: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      marginTop: 8,
      paddingTop: 10,
      borderTopWidth: 1,
      borderTopColor: colors.border,
    },
    rideCodeLabel: {
      fontSize: 12,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.textDim,
      textTransform: 'uppercase',
      letterSpacing: 0.5,
    },
    rideCodeValue: {
      fontSize: 12,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
      letterSpacing: 0.5,
    },
    // Fare table
    fareTable: {
      gap: 4,
    },
    lineRow: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'center',
      paddingVertical: 6,
    },
    lineLabel: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.text,
    },
    lineValue: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.text,
    },
    divider: {
      height: 1,
      backgroundColor: colors.border,
      marginVertical: 6,
    },
    totalRow: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'center',
      paddingVertical: 6,
    },
    totalLabel: {
      fontSize: 16,
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text,
    },
    totalValue: {
      fontSize: 18,
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.primary,
    },
    // Email note
    emailNote: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8,
      backgroundColor: colors.surfaceLight,
      borderRadius: 12,
      padding: 14,
      borderWidth: 1,
      borderColor: colors.border,
    },
    emailNoteText: {
      fontSize: 13,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
      flex: 1,
    },
  });
}
