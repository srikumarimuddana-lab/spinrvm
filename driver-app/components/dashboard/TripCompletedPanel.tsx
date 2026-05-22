import React, { useState, useEffect, useMemo } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, TextInput, Share, Alert, BackHandler } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useLanguageStore } from '../../store/languageStore';

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

    onDone();
  };

  return (
    <View style={styles.completedOverlay}>
      <View style={styles.dimBg} />
      <View style={[styles.completedPanel, { paddingBottom: Math.max(insets.bottom, 16) + 16 }]}>
        <View style={styles.completedIcon}>
          <Ionicons name="checkmark-circle" size={56} color="#34C759" />
        </View>
        <Text style={styles.completedTitle} accessibilityRole="header">{t('tripCompleted.title')}</Text>

        {/* Hero earnings */}
        <View style={styles.earningsHero}>
          <Text style={styles.earningsHeroLabel}>YOUR EARNINGS</Text>
          <Text style={styles.earningsHeroAmount} allowFontScaling={false}>
            ${money(completedRide?.driver_earnings)}
          </Text>
          <View style={styles.keepBadge}>
            <Ionicons name="checkmark-circle" size={12} color="#34C759" />
            <Text style={styles.keepBadgeText}>You kept 100% — $0 commission</Text>
          </View>
        </View>

        {/* Fare breakdown */}
        <View style={styles.fareBreakdown}>
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
          {n(completedRide?.tip_amount) > 0 && (
            <>
              <View style={styles.fareDivider} />
              <View style={styles.fareRow}>
                <Text allowFontScaling={false} style={styles.fareItemLabel}>{t('tripCompleted.tip')}</Text>
                <Text style={[styles.fareItemValue, { color: '#34C759' }]}>${money(completedRide?.tip_amount)}</Text>
              </View>
            </>
          )}
        </View>

        {/* Trip stats */}
        <View style={styles.tripStats}>
          <View style={styles.tripStat}>
            <Ionicons name="speedometer" size={18} color={colors.textDim} />
            <Text style={styles.tripStatValue}>{n(completedRide?.distance_km).toFixed(1)} km</Text>
          </View>
          <View style={styles.tripStat}>
            <Ionicons name="time" size={18} color={colors.textDim} />
            <Text style={styles.tripStatValue}>{n(completedRide?.duration_minutes)} min</Text>
          </View>
        </View>

        {/* Share Receipt */}
        <TouchableOpacity
          style={styles.shareReceiptBtn}
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
              Alert.alert('Receipt', receipt);
            }
          }}
          activeOpacity={0.7}
        >
          <Ionicons name="receipt-outline" size={16} color={colors.primary} />
          <Text style={styles.shareReceiptText}>{t('tripCompleted.shareReceipt')}</Text>
        </TouchableOpacity>

        {/* Rate your rider */}
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
                    size={36}
                    color={star <= rating ? colors.gold : colors.border}
                  />
                </TouchableOpacity>
              ))}
            </View>
            {rating > 0 && (
              <TextInput
                style={[styles.commentInput, { minHeight: undefined, maxHeight: undefined, flex: 1 }]}
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

        {/* Post-trip chat */}
        {completedRide.id && (
          <TouchableOpacity
            style={styles.messageRiderBtn}
            onPress={() => router.push(`/driver/chat?rideId=${completedRide.id}` as any)}
            activeOpacity={0.7}
            accessibilityRole="button"
            accessibilityLabel="Message Rider"
          >
            <Ionicons name="chatbubble-ellipses-outline" size={16} color="#3B82F6" />
            <Text style={styles.messageRiderText}>Message Rider</Text>
          </TouchableOpacity>
        )}

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
      </LinearGradient>
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
      ...StyleSheet.absoluteFillObject as any,
      backgroundColor: 'rgba(0,0,0,0.3)',
    },
    completedPanel: {
      backgroundColor: colors.surface,
      borderTopLeftRadius: 28,
      borderTopRightRadius: 28,
      padding: 20,
      alignItems: 'center',
      shadowColor: '#000',
      shadowOffset: { width: 0, height: -8 },
      shadowOpacity: 0.12,
      shadowRadius: 20,
      elevation: 20,
    },
    completedIcon: {
      marginBottom: 10,
    },
    completedTitle: {
      fontSize: 20,
      fontWeight: '700',
      color: colors.text,
      marginBottom: 12,
    },
    earningsHero: {
      alignItems: 'center',
      marginBottom: 16,
    },
    earningsHeroLabel: {
      fontSize: 10,
      fontWeight: '600',
      color: colors.textDim,
      letterSpacing: 0.8,
    },
    earningsHeroAmount: {
      fontSize: 40,
      fontWeight: '800',
      color: '#34C759',
      letterSpacing: -1,
      marginTop: 2,
    },
    keepBadge: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 4,
      backgroundColor: '#34C75915',
      paddingHorizontal: 10,
      paddingVertical: 4,
      borderRadius: 10,
      marginTop: 6,
    },
    keepBadgeText: {
      fontSize: 11,
      fontWeight: '600',
      color: '#34C759',
    },
    fareBreakdown: {
      width: '100%',
      backgroundColor: colors.surfaceLight,
      borderRadius: 16,
      padding: 16,
      marginBottom: 16,
    },
    fareRow: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      marginBottom: 8,
    },
    fareItemLabel: {
      color: colors.textDim,
      fontSize: 14,
    },
    fareItemValue: {
      color: colors.text,
      fontSize: 14,
      fontWeight: '600',
    },
    fareDivider: {
      height: 1,
      backgroundColor: colors.border,
      marginVertical: 12,
    },
    fareEarningsLabel: {
      color: '#34C759',
      fontSize: 14,
      fontWeight: '700',
    },
    fareEarningsValue: {
      color: '#34C759',
      fontSize: 16,
      fontWeight: '800',
    },
    tripStats: {
      flexDirection: 'row',
      gap: 24,
      marginBottom: 20,
    },
    tripStat: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8,
      backgroundColor: colors.surfaceLight,
      paddingHorizontal: 16,
      paddingVertical: 10,
      borderRadius: 12,
    },
    tripStatValue: {
      color: colors.text,
      fontSize: 14,
      fontWeight: '600',
    },
    ratingSection: {
      width: '100%',
      alignItems: 'center',
      marginBottom: 20,
      backgroundColor: colors.surfaceLight,
      borderRadius: 16,
      padding: 16,
    },
    ratingLabel: {
      color: colors.text,
      fontSize: 15,
      fontWeight: '600',
      marginBottom: 12,
    },
    starsRow: {
      flexDirection: 'row',
      gap: 8,
      marginBottom: 4,
    },
    commentInput: {
      width: '100%',
      marginTop: 12,
      backgroundColor: colors.background,
      borderRadius: 12,
      padding: 12,
      color: colors.text,
      fontSize: 14,
      minHeight: 60,
      maxHeight: 100,
      borderWidth: 1,
      borderColor: colors.border,
    },
    shareReceiptBtn: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 6,
      paddingVertical: 10,
      marginBottom: 16,
      backgroundColor: colors.surfaceLight,
      borderRadius: 12,
      width: '100%',
    },
    shareReceiptText: {
      color: colors.primary,
      fontSize: 14,
      fontWeight: '600',
    },
    messageRiderBtn: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 6,
      paddingVertical: 10,
      marginBottom: 12,
      backgroundColor: colors.infoBg,
      borderRadius: 12,
      borderWidth: 1,
      borderColor: colors.info,
      width: '100%',
    },
    messageRiderText: {
      color: colors.info,
      fontSize: 14,
      fontWeight: '600',
    },
    doneBtn: {
      borderRadius: 16,
      overflow: 'hidden',
      width: '100%',
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
