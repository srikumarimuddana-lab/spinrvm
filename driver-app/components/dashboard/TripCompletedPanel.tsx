import React, { useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, TextInput } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';
import SpinrConfig from '@shared/config/spinr.config';

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
  driver_earnings?: number;
  distance_km?: number;
  duration_minutes?: number;
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
        <Text style={styles.completedTitle}>Trip Completed!</Text>

        {/* Fare breakdown */}
        <View style={styles.fareBreakdown}>
          <View style={styles.fareRow}>
            <Text style={styles.fareItemLabel}>Base Fare</Text>
            <Text style={styles.fareItemValue}>${(completedRide.base_fare || 0).toFixed(2)}</Text>
          </View>
          <View style={styles.fareRow}>
            <Text style={styles.fareItemLabel}>Distance</Text>
            <Text style={styles.fareItemValue}>${(completedRide.distance_fare || 0).toFixed(2)}</Text>
          </View>
          <View style={styles.fareRow}>
            <Text style={styles.fareItemLabel}>Time</Text>
            <Text style={styles.fareItemValue}>${(completedRide.time_fare || 0).toFixed(2)}</Text>
          </View>
          <View style={styles.fareDivider} />
          <View style={styles.fareRow}>
            <Text style={styles.fareEarningsLabel}>Your Earnings</Text>
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

        {/* Rate your rider */}
        {!submitted && (
          <View style={styles.ratingSection}>
            <Text style={styles.ratingLabel}>How was your rider?</Text>
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
                placeholder="Any comments? (optional)"
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

        <TouchableOpacity
          style={[styles.doneBtn, submitting && { opacity: 0.6 }]}
          onPress={handleSubmit}
          disabled={submitting}
        >
          <LinearGradient colors={[COLORS.accent, COLORS.accentDim]} style={styles.actionGradient}>
            <Text style={styles.actionBtnText}>
              {submitting ? 'Submitting...' : rating > 0 ? 'Rate & Done' : 'Skip Rating'}
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
