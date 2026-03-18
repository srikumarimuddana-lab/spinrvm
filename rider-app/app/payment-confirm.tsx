import React, { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  ActivityIndicator,
  Alert,
  TextInput,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useRideStore } from '../store/rideStore';
import SpinrConfig from '@shared/config/spinr.config';
import api from '@shared/api/client';

const PAYMENT_METHODS = [
  { id: 'card', name: 'Credit Card', icon: 'card', last4: '4242' },
  { id: 'cash', name: 'Cash', icon: 'cash', last4: null },
];

export default function PaymentConfirmScreen() {
  const router = useRouter();
  const { pickup, dropoff, selectedVehicle, estimates, createRide, isLoading, scheduledTime } = useRideStore();
  const [selectedPayment, setSelectedPayment] = useState('card');
  const [promoExpanded, setPromoExpanded] = useState(false);
  const [promoCode, setPromoCode] = useState('');
  const [promoDiscount, setPromoDiscount] = useState(0);
  const [promoValidating, setPromoValidating] = useState(false);
  const [promoApplied, setPromoApplied] = useState(false);
  const [promoMessage, setPromoMessage] = useState('');

  const selectedEstimate = estimates.find((e) => e.vehicle_type.id === selectedVehicle?.id);

  const handleBookRide = async () => {
    try {
      const ride = await createRide(selectedPayment);
      router.replace('/driver-arriving?rideId=' + ride.id);
    } catch (error: any) {
      Alert.alert('Error', error.message || 'Failed to book ride');
    }
  };

  const handleApplyPromo = async () => {
    const code = promoCode.trim();
    if (!code) return;
    setPromoValidating(true);
    setPromoMessage('');
    try {
      const fare = selectedEstimate?.total_fare || 0;
      const res = await api.post('/promo/validate', { code, ride_fare: fare });
      setPromoDiscount(res.data.discount_amount);
      setPromoApplied(true);
      setPromoMessage(`-$${res.data.discount_amount.toFixed(2)} discount applied!`);
    } catch (error: any) {
      const msg = error?.response?.data?.detail || 'Invalid promo code';
      setPromoMessage(msg);
      setPromoDiscount(0);
      setPromoApplied(false);
    } finally {
      setPromoValidating(false);
    }
  };

  const totalFare = (selectedEstimate?.total_fare || 0) - promoDiscount;

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity style={styles.backButton} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color="#1A1A1A" />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Confirm booking</Text>
        <View style={{ width: 44 }} />
      </View>

      <ScrollView style={styles.content} showsVerticalScrollIndicator={false}>
        {/* Ride Summary */}
        <View style={styles.rideSummary}>
          <View style={styles.vehicleInfo}>
            <View style={styles.vehicleIcon}>
              <Ionicons name="car" size={28} color={SpinrConfig.theme.colors.primary} />
            </View>
            <View style={styles.vehicleDetails}>
              <Text style={styles.vehicleName}>{selectedVehicle?.name}</Text>
              <Text style={styles.vehicleDesc}>{selectedEstimate?.duration_minutes} min • {selectedEstimate?.distance_km} km</Text>
            </View>
            <Text style={styles.totalPrice}>${selectedEstimate?.total_fare.toFixed(2)}</Text>
          </View>

          <View style={styles.routeContainer}>
            <View style={styles.routePoint}>
              <View style={[styles.routeDot, { backgroundColor: '#10B981' }]} />
              <View style={styles.routeInfo}>
                <Text style={styles.routeLabel}>Pickup</Text>
                <Text style={styles.routeAddress} numberOfLines={1}>{pickup?.address}</Text>
              </View>
            </View>
            <View style={styles.routeLine} />
            <View style={styles.routePoint}>
              <View style={[styles.routeDot, { backgroundColor: SpinrConfig.theme.colors.primary }]} />
              <View style={styles.routeInfo}>
                <Text style={styles.routeLabel}>Dropoff</Text>
                <Text style={styles.routeAddress} numberOfLines={1}>{dropoff?.address}</Text>
              </View>
            </View>
          </View>
        </View>

        {/* Fare Breakdown */}
        {selectedEstimate && (
          <View style={styles.fareBreakdown}>
            <Text style={styles.fareBreakdownTitle}>Fare Breakdown</Text>

            <View style={styles.fareRow}>
              <Text style={styles.fareLabel}>Base fare</Text>
              <Text style={styles.fareValue}>${selectedEstimate.base_fare.toFixed(2)}</Text>
            </View>
            <View style={styles.fareRow}>
              <Text style={styles.fareLabel}>Distance ({selectedEstimate.distance_km} km)</Text>
              <Text style={styles.fareValue}>${selectedEstimate.distance_fare.toFixed(2)}</Text>
            </View>
            <View style={styles.fareRow}>
              <Text style={styles.fareLabel}>Time ({selectedEstimate.duration_minutes} min)</Text>
              <Text style={styles.fareValue}>${selectedEstimate.time_fare.toFixed(2)}</Text>
            </View>
            <View style={styles.fareDivider} />
            <View style={styles.fareRow}>
              <Text style={styles.fareLabel}>Insurance fee</Text>
              <Text style={styles.fareValue}>${(selectedEstimate.total_fare * 0.02).toFixed(2)}</Text>
            </View>
            <View style={styles.fareRow}>
              <Text style={styles.fareLabel}>City fee</Text>
              <Text style={styles.fareValue}>$0.50</Text>
            </View>
            <View style={styles.fareRow}>
              <Text style={styles.fareLabel}>GST/PST (11%)</Text>
              <Text style={styles.fareValue}>${(selectedEstimate.total_fare * 0.11).toFixed(2)}</Text>
            </View>
            <View style={styles.fareRow}>
              <Text style={styles.fareLabel}>Platform fee</Text>
              <Text style={styles.fareValue}>${selectedEstimate.booking_fee.toFixed(2)}</Text>
            </View>
            {(selectedEstimate as any).surge_multiplier > 1.0 && (
              <View style={styles.fareRow}>
                <Text style={[styles.fareLabel, { color: '#EF4444' }]}>Surge ({(selectedEstimate as any).surge_multiplier}x)</Text>
                <Text style={[styles.fareValue, { color: '#EF4444' }]}>Applied</Text>
              </View>
            )}
            <View style={styles.fareDivider} />
            <View style={styles.fareRow}>
              <Text style={styles.fareTotalLabel}>Estimated Total</Text>
              <Text style={styles.fareTotalValue}>
                ${(
                  selectedEstimate.total_fare +
                  selectedEstimate.total_fare * 0.02 +
                  0.50 +
                  selectedEstimate.total_fare * 0.11
                ).toFixed(2)}
              </Text>
            </View>
          </View>
        )}

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Payment Method</Text>
          {PAYMENT_METHODS.map((method) => (
            <TouchableOpacity
              key={method.id}
              style={[
                styles.paymentOption,
                selectedPayment === method.id && styles.paymentOptionSelected,
              ]}
              onPress={() => setSelectedPayment(method.id)}
            >
              <View style={styles.paymentIconContainer}>
                <Ionicons
                  name={method.icon as any}
                  size={24}
                  color={selectedPayment === method.id ? SpinrConfig.theme.colors.primary : '#666'}
                />
              </View>
              <View style={styles.paymentInfo}>
                <Text style={styles.paymentName}>{method.name}</Text>
                {method.last4 && (
                  <Text style={styles.paymentDetails}>•••• {method.last4}</Text>
                )}
              </View>
              {selectedPayment === method.id && (
                <View style={styles.paymentCheck}>
                  <Ionicons name="checkmark" size={18} color="#FFFFFF" />
                </View>
              )}
            </TouchableOpacity>
          ))}

          <TouchableOpacity style={styles.addPaymentButton}>
            <Ionicons name="add" size={20} color={SpinrConfig.theme.colors.primary} />
            <Text style={styles.addPaymentText}>Add Payment Method</Text>
          </TouchableOpacity>
        </View>

        {/* Promo Code */}
        {!promoExpanded ? (
          <TouchableOpacity style={styles.promoButton} onPress={() => setPromoExpanded(true)}>
            <Ionicons name="pricetag" size={20} color={SpinrConfig.theme.colors.primary} />
            <Text style={styles.promoText}>
              {promoApplied ? `Promo applied: -$${promoDiscount.toFixed(2)}` : 'Add promo code'}
            </Text>
            <Ionicons name="chevron-forward" size={20} color="#999" />
          </TouchableOpacity>
        ) : (
          <View style={styles.promoSection}>
            <Text style={styles.promoSectionTitle}>Promo Code</Text>
            <View style={styles.promoInputRow}>
              <TextInput
                style={styles.promoInput}
                placeholder="Enter code"
                placeholderTextColor="#999"
                value={promoCode}
                onChangeText={(t) => { setPromoCode(t.toUpperCase()); setPromoApplied(false); setPromoMessage(''); }}
                autoCapitalize="characters"
                autoFocus
              />
              <TouchableOpacity
                style={[styles.promoApplyButton, (!promoCode.trim() || promoValidating) && styles.promoApplyDisabled]}
                onPress={handleApplyPromo}
                disabled={!promoCode.trim() || promoValidating}
              >
                {promoValidating ? (
                  <ActivityIndicator color="#FFF" size="small" />
                ) : (
                  <Text style={styles.promoApplyText}>Apply</Text>
                )}
              </TouchableOpacity>
            </View>
            {promoMessage ? (
              <Text style={[styles.promoMessage, promoApplied && styles.promoMessageSuccess]}>
                {promoMessage}
              </Text>
            ) : null}
          </View>
        )}
      </ScrollView>

      {/* Book Button */}
      <View style={styles.footer}>
        <View style={styles.totalRow}>
          <Text style={styles.totalLabel}>Subtotal</Text>
          <Text style={styles.totalAmount}>${selectedEstimate?.total_fare.toFixed(2)}</Text>
        </View>
        {promoDiscount > 0 && (
          <View style={styles.discountRow}>
            <Text style={styles.discountLabel}>Promo discount</Text>
            <Text style={styles.discountAmount}>-${promoDiscount.toFixed(2)}</Text>
          </View>
        )}
        {promoDiscount > 0 && (
          <View style={[styles.totalRow, { marginTop: 4 }]}>
            <Text style={[styles.totalLabel, { fontFamily: 'PlusJakartaSans_700Bold', color: '#1A1A1A' }]}>Total</Text>
            <Text style={styles.totalAmount}>${totalFare.toFixed(2)}</Text>
          </View>
        )}
        {scheduledTime && (
          <View style={styles.scheduledBadge}>
            <Ionicons name="calendar-outline" size={16} color={SpinrConfig.theme.colors.primary} />
            <Text style={styles.scheduledText}>
              Scheduled: {scheduledTime.toLocaleDateString('en-CA', { weekday: 'short', month: 'short', day: 'numeric' })}{' '}
              at {scheduledTime.toLocaleTimeString('en-CA', { hour: '2-digit', minute: '2-digit' })}
            </Text>
          </View>
        )}
        <TouchableOpacity
          style={styles.bookButton}
          onPress={handleBookRide}
          disabled={isLoading}
          activeOpacity={0.8}
        >
          {isLoading ? (
            <ActivityIndicator color="#FFFFFF" />
          ) : (
            <>
              <Text style={styles.bookButtonText}>
                {scheduledTime ? 'Schedule' : 'Book'} {selectedVehicle?.name}
              </Text>
              <Ionicons name="arrow-forward" size={20} color="#FFFFFF" />
            </>
          )}
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#F5F5F5',
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 12,
    backgroundColor: '#FFFFFF',
    borderBottomWidth: 1,
    borderBottomColor: '#F0F0F0',
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
    color: '#1A1A1A',
  },
  content: {
    flex: 1,
  },
  rideSummary: {
    backgroundColor: '#FFFFFF',
    marginBottom: 12,
    padding: 20,
  },
  vehicleInfo: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 20,
  },
  vehicleIcon: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: '#FFF0F0',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 14,
  },
  vehicleDetails: {
    flex: 1,
  },
  vehicleName: {
    fontSize: 18,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
  },
  vehicleDesc: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#666',
  },
  totalPrice: {
    fontSize: 22,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: SpinrConfig.theme.colors.primary,
  },
  routeContainer: {
    paddingTop: 16,
    borderTopWidth: 1,
    borderTopColor: '#F0F0F0',
  },
  routePoint: {
    flexDirection: 'row',
    alignItems: 'flex-start',
  },
  routeDot: {
    width: 12,
    height: 12,
    borderRadius: 6,
    marginTop: 4,
    marginRight: 12,
  },
  routeLine: {
    width: 2,
    height: 24,
    backgroundColor: '#E0E0E0',
    marginLeft: 5,
  },
  routeInfo: {
    flex: 1,
  },
  routeLabel: {
    fontSize: 12,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#999',
    marginBottom: 2,
  },
  routeAddress: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#1A1A1A',
  },
  section: {
    backgroundColor: '#FFFFFF',
    padding: 20,
    marginBottom: 12,
  },
  sectionTitle: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
    marginBottom: 16,
  },
  paymentOption: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 16,
    marginBottom: 12,
    backgroundColor: '#F9F9F9',
    borderRadius: 12,
    borderWidth: 2,
    borderColor: 'transparent',
  },
  paymentOptionSelected: {
    backgroundColor: '#FFF5F5',
    borderColor: SpinrConfig.theme.colors.primary,
  },
  paymentIconContainer: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: '#FFFFFF',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 12,
  },
  paymentInfo: {
    flex: 1,
  },
  paymentName: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
  },
  paymentDetails: {
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#666',
  },
  paymentCheck: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: SpinrConfig.theme.colors.primary,
    justifyContent: 'center',
    alignItems: 'center',
  },
  addPaymentButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 12,
  },
  addPaymentText: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: SpinrConfig.theme.colors.primary,
    marginLeft: 8,
  },
  promoButton: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FFFFFF',
    padding: 20,
    marginBottom: 20,
  },
  promoText: {
    flex: 1,
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#1A1A1A',
    marginLeft: 12,
  },
  footer: {
    backgroundColor: '#FFFFFF',
    paddingHorizontal: 20,
    paddingVertical: 16,
    borderTopWidth: 1,
    borderTopColor: '#F0F0F0',
  },
  totalRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
  },
  totalLabel: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#666',
  },
  totalAmount: {
    fontSize: 24,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
  },
  bookButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: SpinrConfig.theme.colors.primary,
    borderRadius: 28,
    paddingVertical: 18,
    gap: 8,
  },
  bookButtonText: {
    fontSize: 17,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#FFFFFF',
  },
  promoSection: {
    backgroundColor: '#FFFFFF',
    padding: 20,
    marginBottom: 20,
  },
  promoSectionTitle: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
    marginBottom: 12,
  },
  promoInputRow: {
    flexDirection: 'row',
    gap: 10,
  },
  promoInput: {
    flex: 1,
    height: 48,
    backgroundColor: '#F5F5F5',
    borderRadius: 12,
    paddingHorizontal: 16,
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
    letterSpacing: 1,
  },
  promoApplyButton: {
    backgroundColor: SpinrConfig.theme.colors.primary,
    paddingHorizontal: 24,
    borderRadius: 12,
    justifyContent: 'center',
    alignItems: 'center',
  },
  promoApplyDisabled: {
    opacity: 0.5,
  },
  promoApplyText: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#FFFFFF',
  },
  promoMessage: {
    marginTop: 8,
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#EF4444',
  },
  promoMessageSuccess: {
    color: '#10B981',
  },
  discountRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
  },
  discountLabel: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#10B981',
  },
  discountAmount: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#10B981',
  },
  scheduledBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    backgroundColor: '#F0F7FF',
    paddingVertical: 10,
    paddingHorizontal: 14,
    borderRadius: 10,
    marginBottom: 12,
  },
  scheduledText: {
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#1A1A1A',
  },
  fareBreakdown: {
    backgroundColor: '#FFFFFF',
    padding: 20,
    marginBottom: 12,
  },
  fareBreakdownTitle: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
    marginBottom: 14,
  },
  fareRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 6,
  },
  fareLabel: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#6B7280',
  },
  fareValue: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#1A1A1A',
  },
  fareDivider: {
    height: 1,
    backgroundColor: '#E5E7EB',
    marginVertical: 10,
  },
  fareTotalLabel: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
  },
  fareTotalValue: {
    fontSize: 18,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: SpinrConfig.theme.colors.primary,
  },
});
