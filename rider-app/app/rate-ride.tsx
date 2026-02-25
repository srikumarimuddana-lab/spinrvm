import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  TextInput,
  Dimensions,
  Alert,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useRideStore } from '../store/rideStore';
import SpinrConfig from '@shared/config/spinr.config';

const { width } = Dimensions.get('window');

const TIP_OPTIONS = [
  { amount: 1, label: '$1' },
  { amount: 3, label: '$3' },
  { amount: 5, label: '$5' },
];

export default function RateRideScreen() {
  const router = useRouter();
  const { rideId } = useLocalSearchParams<{ rideId: string }>();
  const { currentRide, currentDriver, fetchRide, rateRide } = useRideStore();

  const [rating, setRating] = useState(5);
  const [comment, setComment] = useState('');
  const [selectedTip, setSelectedTip] = useState<number | null>(null);
  const [customTip, setCustomTip] = useState('');
  const [showCustomTip, setShowCustomTip] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (rideId) {
      fetchRide(rideId);
    }
  }, [rideId]);

  const handleStarPress = (star: number) => {
    setRating(star);
  };

  const handleTipSelect = (amount: number) => {
    setSelectedTip(amount);
    setShowCustomTip(false);
    setCustomTip('');
  };

  const handleCustomTipPress = () => {
    setSelectedTip(null);
    setShowCustomTip(true);
  };

  const getTipAmount = (): number => {
    if (selectedTip !== null) return selectedTip;
    if (customTip) return parseFloat(customTip) || 0;
    return 0;
  };

  const handleSubmit = async () => {
    if (!rideId) return;

    setIsSubmitting(true);
    try {
      const tipAmount = getTipAmount();
      await rateRide(rideId, rating, comment, tipAmount);

      Alert.alert(
        'Thank you!',
        tipAmount > 0
          ? `Your ${rating}-star rating and $${tipAmount.toFixed(2)} tip have been submitted.`
          : `Your ${rating}-star rating has been submitted.`,
        [
          {
            text: 'Done',
            onPress: () => router.replace('/(tabs)')
          }
        ]
      );
    } catch (error) {
      Alert.alert('Error', 'Failed to submit rating. Please try again.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const totalFare = currentRide?.total_fare || 0;
  const tipAmount = getTipAmount();
  const finalTotal = totalFare + tipAmount;

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        style={styles.keyboardView}
      >
        <ScrollView
          style={styles.scrollView}
          contentContainerStyle={styles.scrollContent}
          showsVerticalScrollIndicator={false}
        >
          {/* Header */}
          <View style={styles.header}>
            <View style={styles.arrivedBadge}>
              <Ionicons name="checkmark-circle" size={20} color="#10B981" />
              <Text style={styles.arrivedText}>You arrived!</Text>
            </View>

            <Text style={styles.fareAmount}>${totalFare.toFixed(2)}</Text>

            <View style={styles.commissionBadge}>
              <Text style={styles.commissionText}>0% commission</Text>
              <Text style={styles.commissionSubtext}>Driver keeps 100% of fare</Text>
            </View>
          </View>

          {/* Driver Card */}
          <View style={styles.driverCard}>
            <View style={styles.driverAvatar}>
              <Ionicons name="person" size={32} color="#666" />
            </View>
            <View style={styles.driverInfo}>
              <Text style={styles.driverName}>{currentDriver?.name || 'Unknown'}</Text>
              <Text style={styles.vehicleInfo}>
                {currentDriver?.vehicle_color || ''} {currentDriver?.vehicle_make || 'Unknown'} {currentDriver?.vehicle_model || 'Vehicle'}
              </Text>
              <View style={styles.driverStats}>
                <View style={styles.statItem}>
                  <Ionicons name="star" size={14} color="#FFB800" />
                  <Text style={styles.statText}>{currentDriver?.rating || 'New'}</Text>
                </View>
                <View style={styles.statDivider} />
                <Text style={styles.plateText}>{currentDriver?.license_plate || 'Pending'}</Text>
              </View>
            </View>
          </View>

          {/* Rating Section */}
          <View style={styles.ratingSection}>
            <Text style={styles.ratingTitle}>How was your ride?</Text>
            <View style={styles.starsContainer}>
              {[1, 2, 3, 4, 5].map((star) => (
                <TouchableOpacity
                  key={star}
                  onPress={() => handleStarPress(star)}
                  style={styles.starButton}
                >
                  <Ionicons
                    name={star <= rating ? 'star' : 'star-outline'}
                    size={40}
                    color={star <= rating ? '#FFB800' : '#D1D5DB'}
                  />
                </TouchableOpacity>
              ))}
            </View>
            <Text style={styles.ratingLabel}>
              {rating === 5 ? 'Excellent!' : rating === 4 ? 'Great!' : rating === 3 ? 'Good' : rating === 2 ? 'Fair' : 'Poor'}
            </Text>
          </View>

          {/* Comment Section */}
          <View style={styles.commentSection}>
            <Text style={styles.commentLabel}>Add a comment (optional)</Text>
            <TextInput
              style={styles.commentInput}
              placeholder="Share your experience..."
              placeholderTextColor="#9CA3AF"
              multiline
              numberOfLines={3}
              value={comment}
              onChangeText={setComment}
              textAlignVertical="top"
            />
          </View>

          {/* Tip Section */}
          <View style={styles.tipSection}>
            <Text style={styles.tipTitle}>Add a tip for {currentDriver?.name?.split(' ')[0] || 'your driver'}</Text>
            <Text style={styles.tipSubtitle}>100% goes to your driver</Text>

            <View style={styles.tipOptions}>
              {TIP_OPTIONS.map((tip) => (
                <TouchableOpacity
                  key={tip.amount}
                  style={[
                    styles.tipButton,
                    selectedTip === tip.amount && styles.tipButtonSelected
                  ]}
                  onPress={() => handleTipSelect(tip.amount)}
                >
                  <Text style={[
                    styles.tipButtonText,
                    selectedTip === tip.amount && styles.tipButtonTextSelected
                  ]}>
                    {tip.label}
                  </Text>
                </TouchableOpacity>
              ))}
              <TouchableOpacity
                style={[
                  styles.tipButton,
                  showCustomTip && styles.tipButtonSelected
                ]}
                onPress={handleCustomTipPress}
              >
                <Text style={[
                  styles.tipButtonText,
                  showCustomTip && styles.tipButtonTextSelected
                ]}>
                  Custom
                </Text>
              </TouchableOpacity>
            </View>

            {showCustomTip && (
              <View style={styles.customTipContainer}>
                <Text style={styles.dollarSign}>$</Text>
                <TextInput
                  style={styles.customTipInput}
                  placeholder="0.00"
                  placeholderTextColor="#9CA3AF"
                  keyboardType="decimal-pad"
                  value={customTip}
                  onChangeText={setCustomTip}
                />
              </View>
            )}
          </View>

          {/* Total */}
          {tipAmount > 0 && (
            <View style={styles.totalSection}>
              <View style={styles.totalRow}>
                <Text style={styles.totalLabel}>Ride fare</Text>
                <Text style={styles.totalValue}>${totalFare.toFixed(2)}</Text>
              </View>
              <View style={styles.totalRow}>
                <Text style={styles.totalLabel}>Tip</Text>
                <Text style={styles.totalValue}>${tipAmount.toFixed(2)}</Text>
              </View>
              <View style={styles.totalDivider} />
              <View style={styles.totalRow}>
                <Text style={styles.grandTotalLabel}>Total</Text>
                <Text style={styles.grandTotalValue}>${finalTotal.toFixed(2)}</Text>
              </View>
            </View>
          )}

          {/* Submit Button */}
          <TouchableOpacity
            style={[styles.submitButton, isSubmitting && styles.submitButtonDisabled]}
            onPress={handleSubmit}
            disabled={isSubmitting}
          >
            <Text style={styles.submitButtonText}>
              {isSubmitting ? 'Submitting...' : tipAmount > 0 ? `Done - Pay $${finalTotal.toFixed(2)}` : 'Done'}
            </Text>
          </TouchableOpacity>

          {/* Skip */}
          <TouchableOpacity
            style={styles.skipButton}
            onPress={() => router.replace('/(tabs)')}
          >
            <Text style={styles.skipButtonText}>Skip rating</Text>
          </TouchableOpacity>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#FFF',
  },
  keyboardView: {
    flex: 1,
  },
  scrollView: {
    flex: 1,
  },
  scrollContent: {
    padding: 20,
    paddingBottom: 40,
  },
  header: {
    alignItems: 'center',
    marginBottom: 24,
  },
  arrivedBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#ECFDF5',
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 20,
    gap: 6,
    marginBottom: 16,
  },
  arrivedText: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#10B981',
  },
  fareAmount: {
    fontSize: 48,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
    marginBottom: 8,
  },
  commissionBadge: {
    alignItems: 'center',
  },
  commissionText: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: SpinrConfig.theme.colors.primary,
  },
  commissionSubtext: {
    fontSize: 12,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#6B7280',
    marginTop: 2,
  },
  driverCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#F9FAFB',
    borderRadius: 16,
    padding: 16,
    marginBottom: 24,
  },
  driverAvatar: {
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: '#E5E7EB',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 14,
  },
  driverInfo: {
    flex: 1,
  },
  driverName: {
    fontSize: 18,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
  },
  vehicleInfo: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#6B7280',
    marginTop: 2,
  },
  driverStats: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 6,
  },
  statItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 3,
  },
  statText: {
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
  },
  statDivider: {
    width: 1,
    height: 12,
    backgroundColor: '#D1D5DB',
    marginHorizontal: 10,
  },
  plateText: {
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#6B7280',
  },
  ratingSection: {
    alignItems: 'center',
    marginBottom: 24,
  },
  ratingTitle: {
    fontSize: 18,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
    marginBottom: 16,
  },
  starsContainer: {
    flexDirection: 'row',
    gap: 8,
  },
  starButton: {
    padding: 4,
  },
  ratingLabel: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#6B7280',
    marginTop: 12,
  },
  commentSection: {
    marginBottom: 24,
  },
  commentLabel: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#374151',
    marginBottom: 8,
  },
  commentInput: {
    backgroundColor: '#F9FAFB',
    borderRadius: 12,
    padding: 14,
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#1A1A1A',
    minHeight: 80,
    borderWidth: 1,
    borderColor: '#E5E7EB',
  },
  tipSection: {
    marginBottom: 24,
  },
  tipTitle: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
    textAlign: 'center',
  },
  tipSubtitle: {
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#6B7280',
    textAlign: 'center',
    marginTop: 4,
    marginBottom: 16,
  },
  tipOptions: {
    flexDirection: 'row',
    justifyContent: 'center',
    gap: 12,
  },
  tipButton: {
    paddingHorizontal: 24,
    paddingVertical: 14,
    borderRadius: 25,
    borderWidth: 1.5,
    borderColor: '#E5E7EB',
    backgroundColor: '#FFF',
  },
  tipButtonSelected: {
    borderColor: SpinrConfig.theme.colors.primary,
    backgroundColor: '#FEF2F2',
  },
  tipButtonText: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#374151',
  },
  tipButtonTextSelected: {
    color: SpinrConfig.theme.colors.primary,
  },
  customTipContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 16,
  },
  dollarSign: {
    fontSize: 24,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#374151',
    marginRight: 4,
  },
  customTipInput: {
    fontSize: 24,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
    width: 100,
    textAlign: 'center',
    borderBottomWidth: 2,
    borderBottomColor: SpinrConfig.theme.colors.primary,
    paddingBottom: 4,
  },
  totalSection: {
    backgroundColor: '#F9FAFB',
    borderRadius: 12,
    padding: 16,
    marginBottom: 24,
  },
  totalRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 8,
  },
  totalLabel: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#6B7280',
  },
  totalValue: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#1A1A1A',
  },
  totalDivider: {
    height: 1,
    backgroundColor: '#E5E7EB',
    marginVertical: 8,
  },
  grandTotalLabel: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
  },
  grandTotalValue: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
  },
  submitButton: {
    backgroundColor: SpinrConfig.theme.colors.primary,
    borderRadius: 28,
    paddingVertical: 16,
    alignItems: 'center',
    marginBottom: 12,
  },
  submitButtonDisabled: {
    opacity: 0.7,
  },
  submitButtonText: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#FFF',
  },
  skipButton: {
    alignItems: 'center',
    paddingVertical: 12,
  },
  skipButtonText: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#6B7280',
  },
});
