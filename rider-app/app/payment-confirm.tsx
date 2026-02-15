import React, { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  ActivityIndicator,
  Alert,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useRideStore } from '../store/rideStore';
import SpinrConfig from '@shared/config/spinr.config';

const PAYMENT_METHODS = [
  { id: 'card', name: 'Credit Card', icon: 'card', last4: '4242' },
  { id: 'cash', name: 'Cash', icon: 'cash', last4: null },
];

export default function PaymentConfirmScreen() {
  const router = useRouter();
  const { pickup, dropoff, selectedVehicle, estimates, createRide, isLoading } = useRideStore();
  const [selectedPayment, setSelectedPayment] = useState('card');

  const selectedEstimate = estimates.find((e) => e.vehicle_type.id === selectedVehicle?.id);

  const handleBookRide = async () => {
    try {
      const ride = await createRide(selectedPayment);
      router.replace({
        pathname: '/driver-arriving',
        params: { rideId: ride.id },
      });
    } catch (error: any) {
      Alert.alert('Error', error.message || 'Failed to book ride');
    }
  };

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

        {/* Payment Methods */}
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
        <TouchableOpacity style={styles.promoButton}>
          <Ionicons name="pricetag" size={20} color={SpinrConfig.theme.colors.primary} />
          <Text style={styles.promoText}>Add promo code</Text>
          <Ionicons name="chevron-forward" size={20} color="#999" />
        </TouchableOpacity>
      </ScrollView>

      {/* Book Button */}
      <View style={styles.footer}>
        <View style={styles.totalRow}>
          <Text style={styles.totalLabel}>Total</Text>
          <Text style={styles.totalAmount}>${selectedEstimate?.total_fare.toFixed(2)}</Text>
        </View>
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
              <Text style={styles.bookButtonText}>Book {selectedVehicle?.name}</Text>
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
});
