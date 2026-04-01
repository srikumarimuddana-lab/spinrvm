import React, { useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView, ActivityIndicator, Platform,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import MapView, { Marker, Polyline, PROVIDER_GOOGLE } from 'react-native-maps';
import MapViewDirections from 'react-native-maps-directions';
import api from '@shared/api/client';
import SpinrConfig from '@shared/config/spinr.config';

const COLORS = SpinrConfig.theme.colors;
const MAP_PROVIDER = Platform.OS === 'android' ? PROVIDER_GOOGLE : undefined;
const GOOGLE_MAPS_API_KEY = process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY;

export default function RideDetailsScreen() {
  const router = useRouter();
  const { rideId } = useLocalSearchParams<{ rideId: string }>();
  const [ride, setRide] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [routeCoords, setRouteCoords] = useState<any[]>([]);
  const mapRef = React.useRef<MapView>(null);

  useEffect(() => {
    if (rideId) fetchRide();
  }, [rideId]);

  const fetchRide = async () => {
    try {
      const res = await api.get(`/rides/${rideId}`);
      setRide(res.data);
    } catch { }
    finally { setLoading(false); }
  };

  const formatDate = (d: string) => {
    try {
      const date = new Date(d);
      return date.toLocaleDateString('en-CA', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })
        + ' at ' + date.toLocaleTimeString('en-CA', { hour: 'numeric', minute: '2-digit' });
    } catch { return d; }
  };

  if (loading) {
    return (
      <SafeAreaView style={[styles.container, { justifyContent: 'center', alignItems: 'center' }]}>
        <ActivityIndicator size="large" color={COLORS.primary} />
      </SafeAreaView>
    );
  }

  if (!ride) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.header}>
          <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
            <Ionicons name="arrow-back" size={24} color="#1A1A1A" />
          </TouchableOpacity>
          <Text style={styles.headerTitle}>Ride Details</Text>
          <View style={{ width: 44 }} />
        </View>
        <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center' }}>
          <Text style={{ color: '#999', fontSize: 16 }}>Ride not found</Text>
        </View>
      </SafeAreaView>
    );
  }

  const isCompleted = ride.status === 'completed';
  const isCancelled = ride.status === 'cancelled';

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color="#1A1A1A" />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Ride Details</Text>
        <View style={{ width: 44 }} />
      </View>

      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        {/* Status Badge */}
        <View style={[styles.statusBadge, { backgroundColor: isCompleted ? '#ECFDF5' : isCancelled ? '#FEF2F2' : '#FEF3C7' }]}>
          <Ionicons
            name={isCompleted ? 'checkmark-circle' : isCancelled ? 'close-circle' : 'time'}
            size={18}
            color={isCompleted ? '#10B981' : isCancelled ? '#EF4444' : '#F59E0B'}
          />
          <Text style={[styles.statusText, { color: isCompleted ? '#065F46' : isCancelled ? '#991B1B' : '#92400E' }]}>
            {isCompleted ? 'Completed' : isCancelled ? 'Cancelled' : ride.status}
          </Text>
          <Text style={styles.statusDate}>{formatDate(ride.created_at)}</Text>
        </View>

        {/* Route Map */}
        {ride.pickup_lat && ride.dropoff_lat && (
          <View style={styles.mapCard}>
            <MapView
              ref={mapRef}
              style={styles.map}
              provider={MAP_PROVIDER}
              scrollEnabled={false}
              zoomEnabled={false}
              rotateEnabled={false}
              initialRegion={{
                latitude: (ride.pickup_lat + ride.dropoff_lat) / 2,
                longitude: (ride.pickup_lng + ride.dropoff_lng) / 2,
                latitudeDelta: Math.abs(ride.pickup_lat - ride.dropoff_lat) * 2.5 + 0.01,
                longitudeDelta: Math.abs(ride.pickup_lng - ride.dropoff_lng) * 2.5 + 0.01,
              }}
            >
              {GOOGLE_MAPS_API_KEY && (
                <MapViewDirections
                  origin={{ latitude: ride.pickup_lat, longitude: ride.pickup_lng }}
                  destination={{ latitude: ride.dropoff_lat, longitude: ride.dropoff_lng }}
                  apikey={GOOGLE_MAPS_API_KEY}
                  strokeWidth={4}
                  strokeColor={COLORS.primary}
                  onReady={(r: any) => {
                    setRouteCoords(r.coordinates);
                    mapRef.current?.fitToCoordinates(r.coordinates, {
                      edgePadding: { top: 30, right: 30, bottom: 30, left: 30 }, animated: false,
                    });
                  }}
                />
              )}
              <Marker coordinate={{ latitude: ride.pickup_lat, longitude: ride.pickup_lng }} anchor={{ x: 0.5, y: 0.5 }}>
                <View style={[styles.pin, { backgroundColor: '#10B981' }]}>
                  <Ionicons name="location" size={14} color="#FFF" />
                </View>
              </Marker>
              <Marker coordinate={{ latitude: ride.dropoff_lat, longitude: ride.dropoff_lng }} anchor={{ x: 0.5, y: 0.5 }}>
                <View style={[styles.pin, { backgroundColor: COLORS.primary }]}>
                  <Ionicons name="flag" size={14} color="#FFF" />
                </View>
              </Marker>
            </MapView>
          </View>
        )}

        {/* Route Details */}
        <View style={styles.routeCard}>
          <View style={styles.routeRow}>
            <View style={styles.routeDots}>
              <View style={[styles.dot, { backgroundColor: '#10B981' }]} />
              <View style={styles.routeLine} />
              <View style={[styles.dot, { backgroundColor: COLORS.primary }]} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.routeLabel}>PICKUP</Text>
              <Text style={styles.routeAddr} numberOfLines={2}>{ride.pickup_address}</Text>
              <View style={{ height: 16 }} />
              <Text style={styles.routeLabel}>DROPOFF</Text>
              <Text style={styles.routeAddr} numberOfLines={2}>{ride.dropoff_address}</Text>
            </View>
          </View>
        </View>

        {/* Fare Card */}
        <View style={styles.fareCard}>
          <Text style={styles.fareTitle}>Fare Breakdown</Text>
          <FareRow label="Base fare" value={`$${(ride.base_fare || 0).toFixed(2)}`} />
          <FareRow label={`Distance (${(ride.distance_km || 0).toFixed(1)} km)`} value={`$${(ride.distance_fare || 0).toFixed(2)}`} />
          <FareRow label={`Time (${ride.duration_minutes || 0} min)`} value={`$${(ride.time_fare || 0).toFixed(2)}`} />
          <FareRow label="Booking fee" value={`$${(ride.booking_fee || 0).toFixed(2)}`} />
          {(ride.tip_amount || 0) > 0 && <FareRow label="Tip" value={`$${ride.tip_amount.toFixed(2)}`} highlight />}
          <View style={styles.fareDivider} />
          <View style={styles.fareRowWrap}>
            <Text style={styles.fareTotalLabel}>Total</Text>
            <Text style={styles.fareTotalValue}>${(ride.total_fare || 0).toFixed(2)}</Text>
          </View>
          <View style={styles.paymentRow}>
            <Ionicons name="card" size={14} color="#666" />
            <Text style={styles.paymentText}>
              {ride.payment_method === 'card' ? 'Card' : ride.payment_method || 'Card'} · {ride.payment_status === 'paid' ? 'Paid' : 'Pending'}
            </Text>
          </View>
        </View>

        {/* Trip Stats */}
        <View style={styles.statsRow}>
          <View style={styles.statCard}>
            <Ionicons name="speedometer-outline" size={22} color="#999" />
            <Text style={styles.statVal}>{(ride.distance_km || 0).toFixed(1)} km</Text>
            <Text style={styles.statLabel}>Distance</Text>
          </View>
          <View style={styles.statCard}>
            <Ionicons name="time-outline" size={22} color="#999" />
            <Text style={styles.statVal}>{ride.duration_minutes || 0} min</Text>
            <Text style={styles.statLabel}>Duration</Text>
          </View>
          <View style={styles.statCard}>
            <Ionicons name="star" size={22} color="#FFB800" />
            <Text style={styles.statVal}>{ride.rider_rating || '—'}</Text>
            <Text style={styles.statLabel}>Your Rating</Text>
          </View>
        </View>

        {/* Help */}
        <TouchableOpacity style={styles.helpBtn} onPress={() => router.push('/support' as any)}>
          <Ionicons name="help-circle-outline" size={20} color={COLORS.primary} />
          <Text style={styles.helpText}>Get help with this ride</Text>
          <Ionicons name="chevron-forward" size={16} color="#CCC" />
        </TouchableOpacity>
      </ScrollView>
    </SafeAreaView>
  );
}

function FareRow({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <View style={styles.fareRowWrap}>
      <Text style={[styles.fareLabel, highlight && { color: '#10B981' }]}>{label}</Text>
      <Text style={[styles.fareValue, highlight && { color: '#10B981' }]}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#FFF' },
  header: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: '#F0F0F0',
  },
  backBtn: { width: 44, height: 44, justifyContent: 'center', alignItems: 'center' },
  headerTitle: { fontSize: 18, fontWeight: '700', color: '#1A1A1A' },
  content: { padding: 20, paddingBottom: 40 },

  statusBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingHorizontal: 16, paddingVertical: 12, borderRadius: 14, marginBottom: 16,
  },
  statusText: { fontSize: 15, fontWeight: '700' },
  statusDate: { flex: 1, fontSize: 12, color: '#999', textAlign: 'right' },

  mapCard: { height: 180, borderRadius: 18, overflow: 'hidden', marginBottom: 16, backgroundColor: '#F0F0F0' },
  map: { flex: 1 },
  pin: {
    width: 28, height: 28, borderRadius: 14, justifyContent: 'center', alignItems: 'center',
    borderWidth: 2, borderColor: '#FFF', elevation: 3,
  },

  routeCard: { backgroundColor: '#F9F9F9', borderRadius: 18, padding: 16, marginBottom: 16 },
  routeRow: { flexDirection: 'row' },
  routeDots: { alignItems: 'center', marginRight: 12, paddingTop: 2 },
  dot: { width: 10, height: 10, borderRadius: 5 },
  routeLine: { width: 2, flex: 1, backgroundColor: '#DDD', marginVertical: 3 },
  routeLabel: { fontSize: 10, fontWeight: '600', color: '#999', letterSpacing: 0.5, marginBottom: 2 },
  routeAddr: { fontSize: 14, fontWeight: '500', color: '#1A1A1A' },

  fareCard: { backgroundColor: '#F9F9F9', borderRadius: 18, padding: 16, marginBottom: 16 },
  fareTitle: { fontSize: 15, fontWeight: '700', color: '#1A1A1A', marginBottom: 12 },
  fareRowWrap: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 5 },
  fareLabel: { fontSize: 14, color: '#666' },
  fareValue: { fontSize: 14, fontWeight: '500', color: '#1A1A1A' },
  fareDivider: { height: 1, backgroundColor: '#E8E8E8', marginVertical: 10 },
  fareTotalLabel: { fontSize: 16, fontWeight: '700', color: '#1A1A1A' },
  fareTotalValue: { fontSize: 18, fontWeight: '800', color: COLORS.primary },
  paymentRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 10 },
  paymentText: { fontSize: 13, color: '#666' },

  statsRow: { flexDirection: 'row', gap: 10, marginBottom: 16 },
  statCard: { flex: 1, backgroundColor: '#F9F9F9', borderRadius: 14, padding: 14, alignItems: 'center' },
  statVal: { fontSize: 18, fontWeight: '700', color: '#1A1A1A', marginTop: 6 },
  statLabel: { fontSize: 11, color: '#999', marginTop: 2 },

  helpBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    backgroundColor: '#F9F9F9', borderRadius: 14, padding: 16,
  },
  helpText: { flex: 1, fontSize: 14, fontWeight: '600', color: COLORS.primary },
});
