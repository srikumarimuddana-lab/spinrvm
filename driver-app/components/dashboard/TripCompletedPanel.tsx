import React, { useState, useEffect, useMemo } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, TextInput, Share, BackHandler, ScrollView } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { ROUTE_PIN_COLORS } from '@shared/constants/routeMapStyle';
import { useLanguageStore } from '../../store/languageStore';
import { showAlert } from '../AlertDialog';

const n = (v: number | string | null | undefined): number => {
  if (v == null) return 0;
  const parsed = typeof v === 'string' ? parseFloat(v) : v;
  return Number.isFinite(parsed) ? Math.round(parsed * 100) / 100 : 0;
};
const money = (v: number | string | null | undefined): string => n(v).toFixed(2);

interface CompletedRide {
  id?: string;
  base_fare?: number;
  distance_fare?: number;
  time_fare?: number;
  booking_fee?: number;
  tip_amount?: number;
  total_fare?: number;
  driver_earnings?: number;
  distance_km?: number;
  duration_minutes?: number;
  pickup_address?: string;
  dropoff_address?: string;
  ride_completed_at?: string;
}

interface TripCompletedPanelProps {
  completedRide: CompletedRide | null;
  onDone: () => void;
  onRateRider?: (rideId: string, rating: number, comment?: string) => Promise<void>;
}

export const TripCompletedPanel: React.FC<TripCompletedPanelProps> = ({
  completedRide,
  onDone,
  onRateRider,
}) => {
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const insets = useSafeAreaInsets();
  const { t } = useLanguageStore();
  const router = useRouter();
  const [rating, setRating] = useState(0);
  const [comment, setComment] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => true);
    return () => sub.remove();
  }, []);

  if (!completedRide) return null;

  const completedAt = completedRide.ride_completed_at
    ? new Date(completedRide.ride_completed_at)
    : new Date();
  const completedAtLabel = completedAt.toLocaleString('en-CA', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
  const rideFareTotal = n(completedRide?.base_fare) + n(completedRide?.distance_fare) + n(completedRide?.time_fare);
  const totalFare = n(completedRide?.total_fare) || rideFareTotal + n(completedRide?.booking_fee) + n(completedRide?.tip_amount);

  const handleSubmit = async () => {
    if (submitting) return;

    // If rating > 0, submit it before closing
    if (rating > 0 && onRateRider && completedRide.id) {
      setSubmitting(true);
      try {
        await onRateRider(completedRide.id, rating, comment.trim() || undefined);
        setSubmitted(true);
      } catch (e) {
        console.log('[TripCompleted] Rate rider failed:', e);
      } finally {
        setSubmitting(false);
      }
    }

    try {
      const { onRideRated } = require('@shared/utils/appRating');
      await onRideRated(rating > 0 ? rating : 5);
    } catch { /* non-critical */ }

    onDone();
  };

  return (
    <View style={styles.completedOverlay}>
      <View style={styles.dimBg} />
      <View style={[styles.completedPanel, { paddingBottom: Math.max(insets.bottom, 16) + 16 }]}>
        <View style={styles.sheetHandle} />
        <ScrollView
          showsVerticalScrollIndicator={false}
          contentInsetAdjustmentBehavior="automatic"
          contentContainerStyle={styles.sheetContent}
        >
          <View style={styles.successHeader}>
            <View style={styles.successIconCircle}>
              <Ionicons name="checkmark" size={28} color={colors.success} />
            </View>
            <Text style={styles.completedTitle} accessibilityRole="header">{t('tripCompleted.title')}</Text>
            <Text style={styles.completedTime}>{completedAtLabel}</Text>
          </View>

          <LinearGradient
            colors={[colors.success, `${colors.success}CC`]}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={styles.earningsHero}
          >
            <Text style={styles.earningsHeroLabel}>{t('tripCompleted.yourEarnings')}</Text>
            <Text style={styles.earningsHeroAmount} allowFontScaling={false}>
              ${money(completedRide?.driver_earnings)}
            </Text>
            <View style={styles.keepBadge}>
              <Ionicons name="shield-checkmark" size={14} color="#fff" />
              <Text style={styles.keepBadgeText}>100% driver earnings</Text>
            </View>
          </LinearGradient>

          <View style={styles.tripStats}>
            <View style={styles.tripStat}>
              <Ionicons name="navigate-outline" size={18} color={colors.info} />
              <Text style={styles.tripStatValue}>{n(completedRide?.distance_km).toFixed(1)} km</Text>
            </View>
            <View style={styles.tripStat}>
              <Ionicons name="time-outline" size={18} color={colors.orange} />
              <Text style={styles.tripStatValue}>{n(completedRide?.duration_minutes)} min</Text>
            </View>
            <View style={styles.tripStat}>
              <Ionicons name="cash-outline" size={18} color={colors.success} />
              <Text style={styles.tripStatValue}>${money(totalFare)}</Text>
            </View>
          </View>

          <View style={styles.routeCard}>
            <View style={styles.routePoint}>
              <View style={styles.pickupDot} />
              <Text style={styles.routeAddress} numberOfLines={1}>{completedRide.pickup_address || t('tripCompleted.receiptPickup')}</Text>
            </View>
            <View style={styles.routeLine} />
            <View style={styles.routePoint}>
              <View style={styles.dropoffDot} />
              <Text style={styles.routeAddress} numberOfLines={1}>{completedRide.dropoff_address || t('tripCompleted.receiptDropoff')}</Text>
            </View>
          </View>

          <View style={styles.fareBreakdown}>
            <View style={styles.sectionHeader}>
              <Text style={styles.sectionTitle}>{t('tripCompleted.rideFare')}</Text>
              <Text style={styles.sectionValue}>${money(rideFareTotal)}</Text>
            </View>
            <View style={styles.fareRow}>
              <Text allowFontScaling={false} style={styles.fareItemLabel}>{t('tripCompleted.baseFare')}</Text>
              <Text style={styles.fareItemValue}>${money(completedRide?.base_fare)}</Text>
            </View>
            <View style={styles.fareRow}>
              <Text allowFontScaling={false} style={styles.fareItemLabel}>{t('tripCompleted.distanceFare')}</Text>
              <Text style={styles.fareItemValue}>${money(completedRide?.distance_fare)}</Text>
            </View>
            <View style={styles.fareRow}>
              <Text allowFontScaling={false} style={styles.fareItemLabel}>{t('tripCompleted.timeFare')}</Text>
              <Text style={styles.fareItemValue}>${money(completedRide?.time_fare)}</Text>
            </View>
            {n(completedRide?.booking_fee) > 0 && (
              <View style={styles.fareRow}>
                <Text allowFontScaling={false} style={styles.fareItemLabel}>{t('tripCompleted.bookingFee')}</Text>
                <Text style={styles.fareItemValue}>${money(completedRide?.booking_fee)}</Text>
              </View>
            )}
            {n(completedRide?.tip_amount) > 0 && (
              <View style={styles.fareRow}>
                <Text allowFontScaling={false} style={styles.fareItemLabel}>{t('tripCompleted.tip')}</Text>
                <Text style={[styles.fareItemValue, styles.positiveValue]}>${money(completedRide?.tip_amount)}</Text>
              </View>
            )}
            <View style={styles.fareDivider} />
            <View style={styles.fareRow}>
              <Text allowFontScaling={false} style={styles.fareEarningsLabel}>{t('tripCompleted.yourEarnings')}</Text>
              <Text style={styles.fareEarningsValue}>${money(completedRide?.driver_earnings)}</Text>
            </View>
          </View>

          {!submitted && (
            <View style={styles.ratingSection}>
              <Text allowFontScaling={false} style={styles.ratingLabel} accessibilityRole="text">{t('tripCompleted.howWasRider')}</Text>
              <View style={styles.starsRow}>
                {[1, 2, 3, 4, 5].map((star) => (
                  <TouchableOpacity
                    key={star}
                    onPress={() => setRating(star)}
                    activeOpacity={0.7}
                    hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }}
                    accessibilityRole="adjustable"
                    accessibilityLabel={`Rate ${star} star${star > 1 ? 's' : ''}`}
                    accessibilityState={{ selected: star <= rating }}
                  >
                    <Ionicons
                      name={star <= rating ? 'star' : 'star-outline'}
                      size={34}
                      color={star <= rating ? colors.gold : colors.border}
                    />
                  </TouchableOpacity>
                ))}
              </View>
              {rating > 0 && (
                <TextInput
                  style={styles.commentInput}
                  placeholder={t('tripCompleted.anyComments')}
                  placeholderTextColor={colors.textDim}
                  value={comment}
                  onChangeText={setComment}
                  multiline
                  numberOfLines={3}
                  maxLength={200}
                  textAlignVertical="top"
                  accessibilityLabel={t('tripCompleted.anyComments')}
                />
              )}
            </View>
          )}

          <View style={styles.secondaryActions}>
            <TouchableOpacity
              style={styles.secondaryBtn}
              accessibilityRole="button"
              accessibilityLabel={t('tripCompleted.shareReceipt')}
              onPress={async () => {
                const date = completedRide.ride_completed_at
                  ? new Date(completedRide.ride_completed_at).toLocaleString('en-CA', {
                      dateStyle: 'medium',
                      timeStyle: 'short',
                    })
                  : new Date().toLocaleString('en-CA', { dateStyle: 'medium', timeStyle: 'short' });

                const receipt = [
                  `🚗 ${t('tripCompleted.receiptTitle')}`,
                  '━━━━━━━━━━━━━━━━━━━━━━━',
                  '',
                  completedRide.pickup_address ? `📍 ${t('tripCompleted.receiptPickup')}: ${completedRide.pickup_address}` : null,
                  completedRide.dropoff_address ? `🏁 ${t('tripCompleted.receiptDropoff')}: ${completedRide.dropoff_address}` : null,
                  `📅 ${date}`,
                  '',
                  `${t('tripCompleted.receiptDistance')}: ${n(completedRide?.distance_km).toFixed(1)} km`,
                  `${t('tripCompleted.receiptDuration')}: ${n(completedRide?.duration_minutes)} min`,
                  '',
                  `── ${t('tripCompleted.receiptFareBreakdown')} ──`,
                  `${t('tripCompleted.baseFare')}:     $${money(completedRide?.base_fare)}`,
                  `${t('tripCompleted.distanceFare')}: $${money(completedRide?.distance_fare)}`,
                  `${t('tripCompleted.timeFare')}:     $${money(completedRide?.time_fare)}`,
                  n(completedRide?.booking_fee) > 0 ? `${t('tripCompleted.bookingFee')}:   $${money(completedRide?.booking_fee)}` : null,
                  n(completedRide?.tip_amount) > 0 ? `${t('tripCompleted.tip')}:           $${money(completedRide?.tip_amount)}` : null,
                  '━━━━━━━━━━━━━━━━━━━━━━━',
                  `${t('tripCompleted.receiptYourEarnings')}: $${money(completedRide?.driver_earnings)}`,
                  '',
                  completedRide.id ? `${t('tripCompleted.receiptTripId')}: ${completedRide.id}` : null,
                  '',
                  t('tripCompleted.receiptFooter'),
                ]
                  .filter(Boolean)
                  .join('\n');

                try {
                  await Share.share({ title: 'Spinr Trip Receipt', message: receipt });
                } catch {
                  showAlert('Receipt', receipt);
                }
              }}
              activeOpacity={0.7}
            >
              <Ionicons name="receipt-outline" size={16} color={colors.primary} />
              <Text style={styles.secondaryBtnText}>{t('tripCompleted.shareReceipt')}</Text>
            </TouchableOpacity>

            {completedRide.id && (
              <TouchableOpacity
                style={styles.secondaryBtn}
                onPress={() => router.push(`/driver/chat?rideId=${completedRide.id}` as any)}
                activeOpacity={0.7}
                accessibilityRole="button"
                accessibilityLabel="Message Rider"
              >
                <Ionicons name="chatbubble-ellipses-outline" size={16} color={colors.info} />
                <Text style={[styles.secondaryBtnText, { color: colors.info }]}>Message Rider</Text>
              </TouchableOpacity>
            )}
          </View>
        </ScrollView>

        <TouchableOpacity
          style={[styles.doneBtn, submitting && { opacity: 0.6 }]}
          onPress={handleSubmit}
          disabled={submitting}
          accessibilityRole="button"
          accessibilityLabel={submitting ? t('tripCompleted.submitting') : rating > 0 ? t('tripCompleted.rateDone') : t('tripCompleted.skipRating')}
          accessibilityState={{ disabled: submitting }}
        >
          <LinearGradient colors={[colors.primary, colors.primaryDark]} style={styles.actionGradient}>
            <Text allowFontScaling={false} style={styles.actionBtnText}>
              {submitting ? t('tripCompleted.submitting') : rating > 0 ? t('tripCompleted.rateDone') : t('tripCompleted.skipRating')}
            </Text>
          </LinearGradient>
        </TouchableOpacity>
      </View>
    </View>
  );
};

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    completedOverlay: {
      position: 'absolute',
      top: 0,
      bottom: 0,
      left: 0,
      right: 0,
      justifyContent: 'flex-end',
    },
    dimBg: {
      position: 'absolute',
      top: 0,
      bottom: 0,
      left: 0,
      right: 0,
      backgroundColor: 'rgba(0,0,0,0.3)',
    },
    completedPanel: {
      backgroundColor: colors.surface,
      borderTopLeftRadius: 22,
      borderTopRightRadius: 22,
      maxHeight: '92%',
      paddingHorizontal: 16,
      paddingTop: 10,
    },
    sheetHandle: {
      width: 42,
      height: 5,
      borderRadius: 999,
      backgroundColor: colors.border,
      alignSelf: 'center',
      marginBottom: 12,
    },
    sheetContent: {
      gap: 12,
      paddingBottom: 16,
    },
    successHeader: {
      alignItems: 'center',
      gap: 4,
      paddingTop: 4,
    },
    successIconCircle: {
      width: 52,
      height: 52,
      borderRadius: 26,
      backgroundColor: colors.successBg,
      justifyContent: 'center',
      alignItems: 'center',
      marginBottom: 4,
    },
    completedTitle: {
      fontSize: 19,
      fontWeight: '800',
      color: colors.text,
    },
    completedTime: {
      color: colors.textSecondary,
      fontSize: 12,
      fontWeight: '600',
      fontVariant: ['tabular-nums'],
    },
    earningsHero: {
      borderRadius: 16,
      padding: 18,
      alignItems: 'center',
    },
    earningsHeroLabel: {
      fontSize: 12,
      fontWeight: '700',
      color: 'rgba(255,255,255,0.9)',
      textTransform: 'uppercase',
      letterSpacing: 0.6,
    },
    earningsHeroAmount: {
      fontSize: 44,
      fontWeight: '900',
      color: '#fff',
      letterSpacing: 0,
      marginTop: 2,
      fontVariant: ['tabular-nums'],
    },
    keepBadge: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
      backgroundColor: 'rgba(255,255,255,0.18)',
      paddingHorizontal: 12,
      paddingVertical: 6,
      borderRadius: 999,
      marginTop: 8,
    },
    keepBadgeText: {
      fontSize: 12,
      fontWeight: '700',
      color: '#fff',
    },
    fareBreakdown: {
      width: '100%',
      backgroundColor: colors.surfaceLight,
      borderRadius: 14,
      padding: 14,
      gap: 10,
      borderWidth: 1,
      borderColor: colors.border,
    },
    sectionHeader: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingBottom: 2,
    },
    sectionTitle: {
      color: colors.text,
      fontSize: 15,
      fontWeight: '800',
    },
    sectionValue: {
      color: colors.text,
      fontSize: 15,
      fontWeight: '800',
      fontVariant: ['tabular-nums'],
    },
    fareRow: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'center',
      gap: 16,
    },
    fareItemLabel: {
      color: colors.textDim,
      fontSize: 14,
    },
    fareItemValue: {
      color: colors.text,
      fontSize: 14,
      fontWeight: '600',
      fontVariant: ['tabular-nums'],
    },
    positiveValue: {
      color: colors.success,
    },
    fareDivider: {
      height: 1,
      backgroundColor: colors.border,
      marginVertical: 2,
    },
    fareEarningsLabel: {
      color: colors.success,
      fontSize: 14,
      fontWeight: '700',
    },
    fareEarningsValue: {
      color: colors.success,
      fontSize: 16,
      fontWeight: '800',
      fontVariant: ['tabular-nums'],
    },
    tripStats: {
      flexDirection: 'row',
      gap: 8,
    },
    tripStat: {
      flex: 1,
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 6,
      backgroundColor: colors.surfaceLight,
      paddingHorizontal: 10,
      paddingVertical: 12,
      borderRadius: 12,
      borderWidth: 1,
      borderColor: colors.border,
    },
    tripStatValue: {
      color: colors.text,
      fontSize: 13,
      fontWeight: '700',
      fontVariant: ['tabular-nums'],
    },
    routeCard: {
      backgroundColor: colors.surfaceLight,
      borderRadius: 14,
      padding: 14,
      borderWidth: 1,
      borderColor: colors.border,
    },
    routePoint: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 10,
    },
    pickupDot: {
      width: 12,
      height: 12,
      borderRadius: 6,
      backgroundColor: ROUTE_PIN_COLORS.pickup,
    },
    dropoffDot: {
      width: 12,
      height: 12,
      borderRadius: 6,
      backgroundColor: ROUTE_PIN_COLORS.dropoff,
    },
    routeLine: {
      width: 2,
      height: 18,
      backgroundColor: colors.border,
      marginLeft: 5,
      marginVertical: 3,
    },
    routeAddress: {
      flex: 1,
      color: colors.text,
      fontSize: 14,
      fontWeight: '600',
    },
    ratingSection: {
      width: '100%',
      backgroundColor: colors.surfaceLight,
      borderRadius: 14,
      padding: 14,
      borderWidth: 1,
      borderColor: colors.border,
    },
    ratingLabel: {
      color: colors.text,
      fontSize: 15,
      fontWeight: '600',
      marginBottom: 12,
      textAlign: 'center',
    },
    starsRow: {
      flexDirection: 'row',
      gap: 14,
      justifyContent: 'center',
    },
    commentInput: {
      width: '100%',
      marginTop: 12,
      backgroundColor: colors.background,
      borderRadius: 10,
      padding: 12,
      color: colors.text,
      fontSize: 14,
      minHeight: 60,
      maxHeight: 100,
      borderWidth: 1,
      borderColor: colors.border,
    },
    secondaryActions: {
      flexDirection: 'row',
      gap: 8,
    },
    secondaryBtn: {
      flex: 1,
      minHeight: 46,
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 6,
      paddingHorizontal: 10,
      backgroundColor: colors.surfaceLight,
      borderRadius: 12,
      borderWidth: 1,
      borderColor: colors.border,
    },
    secondaryBtnText: {
      color: colors.primary,
      fontSize: 13,
      fontWeight: '700',
    },
    doneBtn: {
      borderRadius: 12,
      overflow: 'hidden',
      width: '100%',
      marginTop: 2,
    },
    actionGradient: {
      paddingVertical: 16,
      alignItems: 'center',
      justifyContent: 'center',
    },
    actionBtnText: {
      fontSize: 16,
      fontWeight: '700',
      color: '#fff',
    },
  });
}

export default TripCompletedPanel;
