import React, { useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, TextInput, Share, Alert } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import SpinrConfig from '@shared/config/spinr.config';
import { useLanguageStore } from '../../store/languageStore';

const COLORS = {
  primary: SpinrConfig.theme.colors.background,
  accent: SpinrConfig.theme.colors.primary,
  accentDim: SpinrConfig.theme.colors.primaryDark,
  surface: SpinrConfig.theme.colors.surface,
  surfaceLight: SpinrConfig.theme.colors.surfaceLight,
  text: SpinrConfig.theme.colors.text,
  textDim: SpinrConfig.theme.colors.textDim,
  border: SpinrConfig.theme.colors.border,
  gold: '#FFD700',
};

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
  const { t } = useLanguageStore();
  const router = useRouter();
  const [rating, setRating] = useState(0);
  const [comment, setComment] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);

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
      <LinearGradient colors={[COLORS.surface, COLORS.primary]} style={styles.completedPanel}>
        <View style={styles.completedIcon}>
          <Ionicons name="checkmark-circle" size={60} color={COLORS.accent} />
        </View>
        <Text style={styles.completedTitle}>{t('tripCompleted.title')}</Text>

        {/* Fare breakdown */}
        <View style={styles.fareBreakdown}>
          <View style={styles.fareRow}>
            <Text style={styles.fareItemLabel}>{t('tripCompleted.baseFare')}</Text>
            <Text style={styles.fareItemValue}>${(completedRide.base_fare || 0).toFixed(2)}</Text>
          </View>
          <View style={styles.fareRow}>
            <Text style={styles.fareItemLabel}>{t('tripCompleted.distanceFare')}</Text>
            <Text style={styles.fareItemValue}>${(completedRide.distance_fare || 0).toFixed(2)}</Text>
          </View>
          <View style={styles.fareRow}>
            <Text style={styles.fareItemLabel}>{t('tripCompleted.timeFare')}</Text>
            <Text style={styles.fareItemValue}>${(completedRide.time_fare || 0).toFixed(2)}</Text>
          </View>
          <View style={styles.fareDivider} />
          <View style={styles.fareRow}>
            <Text style={styles.fareEarningsLabel}>{t('tripCompleted.yourEarnings')}</Text>
            <Text style={styles.fareEarningsValue}>
              ${(completedRide.driver_earnings || 0).toFixed(2)}
            </Text>
          </View>
        </View>

        {/* Trip stats */}
        <View style={styles.tripStats}>
          <View style={styles.tripStat}>
            <Ionicons name="speedometer" size={18} color={COLORS.textDim} />
            <Text style={styles.tripStatValue}>{(completedRide.distance_km || 0).toFixed(1)} km</Text>
          </View>
          <View style={styles.tripStat}>
            <Ionicons name="time" size={18} color={COLORS.textDim} />
            <Text style={styles.tripStatValue}>{completedRide.duration_minutes || 0} min</Text>
          </View>
        </View>

        {/* Share Receipt */}
        <TouchableOpacity
          style={styles.shareReceiptBtn}
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
              `${t('tripCompleted.receiptDistance')}: ${(completedRide.distance_km || 0).toFixed(1)} km`,
              `${t('tripCompleted.receiptDuration')}: ${completedRide.duration_minutes || 0} min`,
              '',
              `── ${t('tripCompleted.receiptFareBreakdown')} ──`,
              `${t('tripCompleted.baseFare')}:     $${(completedRide.base_fare || 0).toFixed(2)}`,
              `${t('tripCompleted.distanceFare')}: $${(completedRide.distance_fare || 0).toFixed(2)}`,
              `${t('tripCompleted.timeFare')}:     $${(completedRide.time_fare || 0).toFixed(2)}`,
              completedRide.booking_fee ? `${t('tripCompleted.bookingFee')}:   $${completedRide.booking_fee.toFixed(2)}` : null,
              completedRide.tip_amount ? `${t('tripCompleted.tip')}:           $${completedRide.tip_amount.toFixed(2)}` : null,
              '━━━━━━━━━━━━━━━━━━━━━━━',
              `${t('tripCompleted.receiptYourEarnings')}: $${(completedRide.driver_earnings || 0).toFixed(2)}`,
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
          <Ionicons name="receipt-outline" size={16} color={COLORS.accent} />
          <Text style={styles.shareReceiptText}>{t('tripCompleted.shareReceipt')}</Text>
        </TouchableOpacity>

        {/* Rate your rider */}
        {!submitted && (
          <View style={styles.ratingSection}>
            <Text style={styles.ratingLabel}>{t('tripCompleted.howWasRider')}</Text>
            <View style={styles.starsRow}>
              {[1, 2, 3, 4, 5].map((star) => (
                <TouchableOpacity
                  key={star}
                  onPress={() => setRating(star)}
                  activeOpacity={0.7}
                  hitSlop={{ top: 8, bottom: 8, left: 4, right: 4 }}
                >
                  <Ionicons
                    name={star <= rating ? 'star' : 'star-outline'}
                    size={36}
                    color={star <= rating ? COLORS.gold : COLORS.border}
                  />
                </TouchableOpacity>
              ))}
            </View>
            {rating > 0 && (
              <TextInput
                style={styles.commentInput}
                placeholder={t('tripCompleted.anyComments')}
                placeholderTextColor={COLORS.textDim}
                value={comment}
                onChangeText={setComment}
                multiline
                maxLength={200}
                textAlignVertical="top"
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
          >
            <Ionicons name="chatbubble-ellipses-outline" size={16} color="#3B82F6" />
            <Text style={styles.messageRiderText}>Message Rider</Text>
          </TouchableOpacity>
        )}

        <TouchableOpacity
          style={[styles.doneBtn, submitting && { opacity: 0.6 }]}
          onPress={handleSubmit}
          disabled={submitting}
        >
          <LinearGradient colors={[COLORS.accent, COLORS.accentDim]} style={styles.actionGradient}>
            <Text style={styles.actionBtnText}>
              {submitting ? t('tripCompleted.submitting') : rating > 0 ? t('tripCompleted.rateDone') : t('tripCompleted.skipRating')}
            </Text>
          </LinearGradient>
        </TouchableOpacity>
      </LinearGradient>
    </View>
  );
};

const styles = StyleSheet.create({
  completedOverlay: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    justifyContent: 'flex-end',
  },
  completedPanel: {
    borderTopLeftRadius: 28,
    borderTopRightRadius: 28,
    padding: 24,
    paddingBottom: 40,
    alignItems: 'center',
  },
  completedIcon: {
    marginBottom: 16,
  },
  completedTitle: {
    fontSize: 24,
    fontWeight: '800',
    color: COLORS.text,
    marginBottom: 24,
  },
  fareBreakdown: {
    width: '100%',
    backgroundColor: COLORS.surfaceLight,
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
    color: COLORS.textDim,
    fontSize: 14,
  },
  fareItemValue: {
    color: COLORS.text,
    fontSize: 14,
    fontWeight: '600',
  },
  fareDivider: {
    height: 1,
    backgroundColor: COLORS.border,
    marginVertical: 12,
  },
  fareEarningsLabel: {
    color: COLORS.accent,
    fontSize: 16,
    fontWeight: '700',
  },
  fareEarningsValue: {
    color: COLORS.accent,
    fontSize: 20,
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
    backgroundColor: COLORS.surfaceLight,
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 12,
  },
  tripStatValue: {
    color: COLORS.text,
    fontSize: 14,
    fontWeight: '600',
  },
  // Rating section
  ratingSection: {
    width: '100%',
    alignItems: 'center',
    marginBottom: 20,
    backgroundColor: COLORS.surfaceLight,
    borderRadius: 16,
    padding: 16,
  },
  ratingLabel: {
    color: COLORS.text,
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
    backgroundColor: COLORS.primary,
    borderRadius: 12,
    padding: 12,
    color: COLORS.text,
    fontSize: 14,
    minHeight: 60,
    maxHeight: 100,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  shareReceiptBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingVertical: 10,
    marginBottom: 16,
    backgroundColor: COLORS.surfaceLight,
    borderRadius: 12,
    width: '100%',
  },
  shareReceiptText: {
    color: COLORS.accent,
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
    backgroundColor: '#EFF6FF',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#DBEAFE',
    width: '100%',
  },
  messageRiderText: {
    color: '#3B82F6',
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

export default TripCompletedPanel;
