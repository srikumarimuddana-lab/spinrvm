import React, { useEffect, useState, useMemo } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView, ActivityIndicator, Platform,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import MapView, { Marker, Polyline, PROVIDER_GOOGLE } from 'react-native-maps';
import MapViewDirections from 'react-native-maps-directions';
import api from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

const MAP_PROVIDER = Platform.OS === 'android' ? PROVIDER_GOOGLE : undefined;
const GOOGLE_MAPS_API_KEY = process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY;

export default function RideDetailsScreen() {
  const router = useRouter();
  const { rideId } = useLocalSearchParams<{ rideId: string }>();
  const { colors, isDark } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
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

  const normalizedBreakdown = useMemo(() => {
    const raw: any[] = ride?.fare_breakdown || [];

    // Consolidate old separate fare lines into a single "Ride fare" line
    const fareLines = raw.filter((l: any) => l.type === 'fare');
    let lines: any[];
    if (fareLines.length <= 1) {
      lines = raw.map((l: any) => l.type === 'fare' ? { ...l, type: 'ride' } : l);
    } else {
      const rideTotal = fareLines.reduce((sum: number, l: any) => sum + parseFloat(String(l.amount || 0)), 0);
      const distKm = ride?.distance_km ? `${parseFloat(ride.distance_km).toFixed(1)} km` : '';
      const rideLine = { label: `Ride fare${distKm ? ` (${distKm})` : ''}`, amount: rideTotal.toFixed(2), type: 'ride' };
      lines = [rideLine, ...raw.filter((l: any) => l.type !== 'fare')];
    }

    // Inject promo discount if ride has one but breakdown doesn't
    const hasDiscount = lines.some((l: any) => l.type === 'discount');
    if (!hasDiscount && ride?.discount_amount && parseFloat(ride.discount_amount) > 0) {
      const promoLabel = ride.promo_code ? `Promo (${ride.promo_code})` : 'Promo discount';
      lines.push({ label: promoLabel, amount: -parseFloat(ride.discount_amount), type: 'discount' });
    }

    // Inject tip if ride has one but breakdown doesn't
    const hasTip = lines.some((l: any) => l.type === 'tip');
    if (!hasTip && ride?.tip_amount && parseFloat(ride.tip_amount) > 0) {
      lines.push({ label: 'Tip', amount: ride.tip_amount, type: 'tip' });
    }

    return lines;
  }, [ride]);

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
        <ActivityIndicator size="large" color={colors.primary} />
      </SafeAreaView>
    );
  }

  if (!ride) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.header}>
          <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
            <Ionicons name="arrow-back" size={24} color={colors.text} />
          </TouchableOpacity>
          <Text style={styles.headerTitle}>Ride Details</Text>
          <View style={{ width: 44 }} />
        </View>
        <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center' }}>
          <Text style={{ color: colors.textDim, fontSize: 16 }}>Ride not found</Text>
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
          <Ionicons name="arrow-back" size={24} color={colors.text} />
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
              userInterfaceStyle={isDark ? "dark" : "light"}
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
                  strokeWidth={0}
                  strokeColor="transparent"
                  onReady={(r: any) => {
                    setRouteCoords(r.coordinates);
                    mapRef.current?.fitToCoordinates(r.coordinates, {
                      edgePadding: { top: 30, right: 30, bottom: 30, left: 30 }, animated: false,
                    });
                  }}
                />
              )}
              {routeCoords.length > 1 && (() => {
                const total = routeCoords.length;
                const SEGS = 15;
                const chunk = Math.max(1, Math.floor(total / SEGS));
                const segments: { coords: any[]; color: string }[] = [];
                for (let i = 0; i < total - 1; i += chunk) {
                  const end = Math.min(i + chunk + 1, total);
                  const t = i / Math.max(total - 1, 1);
                  const r = Math.round(255 + (238 - 255) * t);
                  const g = Math.round(149 + (43 - 149) * t);
                  const b = Math.round(0 + (43 - 0) * t);
                  segments.push({ coords: routeCoords.slice(i, end), color: `rgb(${r},${g},${b})` });
                }
                return segments.map((seg, idx) => (
                  <Polyline
                    key={`rd-seg-${idx}`}
                    coordinates={seg.coords}
                    strokeWidth={4}
                    strokeColor={seg.color}
                    lineCap="round"
                    lineJoin="round"
                  />
                ));
              })()}
              <Marker coordinate={{ latitude: ride.pickup_lat, longitude: ride.pickup_lng }} anchor={{ x: 0.5, y: 0.5 }}>
                <View style={[styles.pin, { backgroundColor: '#10B981' }]}>
                  <Ionicons name="location" size={14} color="#FFF" />
                </View>
              </Marker>
              <Marker coordinate={{ latitude: ride.dropoff_lat, longitude: ride.dropoff_lng }} anchor={{ x: 0.5, y: 0.5 }}>
                <View style={[styles.pin, { backgroundColor: '#EF4444' }]}>
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
              <View style={[styles.dot, { backgroundColor: colors.primary }]} />
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

        {/* Fare breakdown — same layout as ride-options */}
        {normalizedBreakdown.length > 0 && (
          <View style={styles.fareBreakdownCard}>
            <Text style={styles.fareBreakdownTitle}>Fare breakdown</Text>
            {normalizedBreakdown.map((line: any, i: number) => (
              line.amount != null ? (
                <View key={i} style={[styles.fareBreakdownRow, line.type === 'ride' && { alignItems: 'flex-start' }]}>
                  {line.type === 'ride' ? (
                    <View style={{ flex: 1 }}>
                      <Text style={styles.fareBreakdownLabel}>{line.label}</Text>
                      <Text style={styles.fareBreakdownDriverBadge}>100% goes to your driver · ride local, support local</Text>
                    </View>
                  ) : line.type === 'discount' ? (
                    <Text style={[styles.fareBreakdownLabel, { color: '#10B981' }]}>{line.label}</Text>
                  ) : line.type === 'tip' ? (
                    <Text style={[styles.fareBreakdownLabel, { color: '#3B82F6' }]}>{line.label}</Text>
                  ) : line.type === 'tax' ? (
                    <Text style={[styles.fareBreakdownLabel, { color: '#6B7280' }]}>{line.label}</Text>
                  ) : (
                    <Text style={styles.fareBreakdownLabel}>{line.label}</Text>
                  )}
                  <Text style={[
                    styles.fareBreakdownValue,
                    line.type === 'discount' && { color: '#10B981' },
                    line.type === 'tip' && { color: '#3B82F6' },
                  ]}>
                    {line.type === 'discount'
                      ? `-$${Math.abs(parseFloat(String(line.amount))).toFixed(2)}`
                      : `$${parseFloat(String(line.amount)).toFixed(2)}`}
                  </Text>
                </View>
              ) : line.type === 'modifier' ? (
                <View key={i} style={[styles.fareBreakdownRow, { gap: 4 }]}>
                  <Ionicons name="flash" size={12} color="#F59E0B" />
                  <Text style={[styles.fareBreakdownLabel, { color: '#F59E0B' }]}>{line.label}</Text>
                </View>
              ) : null
            ))}
            <View style={[styles.fareBreakdownRow, styles.fareBreakdownTotal]}>
              <Text style={styles.fareBreakdownTotalLabel}>You paid</Text>
              <Text style={styles.fareBreakdownTotalValue}>${normalizedBreakdown.reduce((sum: number, l: any) => l.amount != null ? sum + parseFloat(String(l.amount)) : sum, 0).toFixed(2)}</Text>
            </View>
            <View style={styles.paymentRow}>
              <Ionicons
                name={ride.payment_method === 'wallet' ? 'wallet' : ride.payment_method === 'company_allowance' ? 'business' : 'card'}
                size={14}
                color={ride.payment_status === 'paid' ? '#10B981' : colors.textDim}
              />
              <Text style={[styles.paymentText, ride.payment_status === 'paid' && { color: '#10B981' }]}>
                {ride.payment_method === 'wallet'
                  ? 'Spinr Wallet'
                  : ride.payment_method === 'company_allowance'
                    ? 'Company Account'
                    : ride.card_last4 ? `Card •••• ${ride.card_last4}` : 'Card'}
                {' · '}
                {ride.payment_status === 'paid' ? 'Paid' : ride.payment_status === 'failed' ? 'Failed' : 'Pending'}
              </Text>
            </View>
          </View>
        )}

        {/* Trip Stats */}
        <View style={styles.statsRow}>
          <View style={styles.statCard}>
            <Ionicons name="speedometer-outline" size={22} color={colors.textDim} />
            <Text style={styles.statVal}>{(ride.distance_km || 0).toFixed(1)} km</Text>
            <Text style={styles.statLabel}>Distance</Text>
          </View>
          <View style={styles.statCard}>
            <Ionicons name="time-outline" size={22} color={colors.textDim} />
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
          <Ionicons name="help-circle-outline" size={20} color={colors.primary} />
          <Text style={styles.helpText}>Get help with this ride</Text>
          <Ionicons name="chevron-forward" size={16} color={colors.border} />
        </TouchableOpacity>
      </ScrollView>
    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.surface },
    header: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      paddingHorizontal: 16, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: colors.border,
    },
    backBtn: { width: 44, height: 44, justifyContent: 'center', alignItems: 'center' },
    headerTitle: { fontSize: 18, fontWeight: '700', color: colors.text },
    content: { padding: 20, paddingBottom: 40 },

    statusBadge: {
      flexDirection: 'row', alignItems: 'center', gap: 8,
      paddingHorizontal: 16, paddingVertical: 12, borderRadius: 14, marginBottom: 16,
    },
    statusText: { fontSize: 15, fontWeight: '700' },
    statusDate: { flex: 1, fontSize: 12, color: colors.textDim, textAlign: 'right' },

    mapCard: { height: 180, borderRadius: 18, overflow: 'hidden', marginBottom: 16, backgroundColor: colors.border },
    map: { flex: 1 },
    pin: {
      width: 28, height: 28, borderRadius: 14, justifyContent: 'center', alignItems: 'center',
      borderWidth: 2, borderColor: '#FFF', elevation: 3,
    },

    routeCard: { backgroundColor: colors.surfaceLight, borderRadius: 18, padding: 16, marginBottom: 16 },
    routeRow: { flexDirection: 'row' },
    routeDots: { alignItems: 'center', marginRight: 12, paddingTop: 2 },
    dot: { width: 10, height: 10, borderRadius: 5 },
    routeLine: { width: 2, flex: 1, backgroundColor: colors.border, marginVertical: 3 },
    routeLabel: { fontSize: 10, fontWeight: '600', color: colors.textDim, letterSpacing: 0.5, marginBottom: 2 },
    routeAddr: { fontSize: 14, fontWeight: '500', color: colors.text },

    fareBreakdownCard: {
      backgroundColor: colors.surfaceLight, borderRadius: 14, padding: 14,
      marginBottom: 16, borderWidth: 1, borderColor: colors.border,
    },
    fareBreakdownTitle: {
      fontSize: 12, fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.textDim, marginBottom: 8,
      textTransform: 'uppercase', letterSpacing: 0.5,
    },
    fareBreakdownRow: {
      flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 3,
    },
    fareBreakdownLabel: {
      fontSize: 13, fontFamily: 'PlusJakartaSans_400Regular', color: colors.text,
    },
    fareBreakdownValue: {
      fontSize: 13, fontFamily: 'PlusJakartaSans_500Medium', color: colors.text,
    },
    fareBreakdownDriverBadge: {
      fontSize: 10, fontFamily: 'PlusJakartaSans_500Medium', color: '#10B981', marginTop: 2,
    },
    fareBreakdownTotal: {
      borderTopWidth: 1, borderTopColor: colors.border, marginTop: 4, paddingTop: 6,
    },
    fareBreakdownTotalLabel: {
      fontSize: 14, fontFamily: 'PlusJakartaSans_700Bold', color: colors.text,
    },
    fareBreakdownTotalValue: {
      fontSize: 14, fontFamily: 'PlusJakartaSans_700Bold', color: colors.primary,
    },
    paymentRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 10 },
    paymentText: { fontSize: 13, fontFamily: 'PlusJakartaSans_500Medium', color: colors.textDim },

    statsRow: { flexDirection: 'row', gap: 10, marginBottom: 16 },
    statCard: { flex: 1, backgroundColor: colors.surfaceLight, borderRadius: 14, padding: 14, alignItems: 'center' },
    statVal: { fontSize: 18, fontWeight: '700', color: colors.text, marginTop: 6 },
    statLabel: { fontSize: 11, color: colors.textDim, marginTop: 2 },

    helpBtn: {
      flexDirection: 'row', alignItems: 'center', gap: 10,
      backgroundColor: colors.surfaceLight, borderRadius: 14, padding: 16,
    },
    helpText: { flex: 1, fontSize: 14, fontWeight: '600', color: colors.primary },
  });
}
