import React, { useEffect, useState, useMemo, useRef } from 'react';
import {
    View,
    Text,
    StyleSheet,
    ScrollView,
    Platform,
    ActivityIndicator,
    TouchableOpacity,
    Dimensions,
    Image,
} from 'react-native';
import MapView, { Marker, Polyline, PROVIDER_GOOGLE } from 'react-native-maps';
import MapViewDirections from 'react-native-maps-directions';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

const MAP_PROVIDER = Platform.OS === 'android' ? PROVIDER_GOOGLE : undefined;
const GOOGLE_MAPS_API_KEY = process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY || '';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useLocalSearchParams, useRouter } from 'expo-router';
import api from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

const { width } = Dimensions.get('window');

export default function RideDetailScreen() {
    const { id } = useLocalSearchParams<{ id: string }>();
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const [ride, setRide] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [fallbackCoords, setFallbackCoords] = useState<{ latitude: number; longitude: number }[]>([]);
    const mapRef = useRef<MapView>(null);
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);

    useEffect(() => {
        if (id) loadRide();
    }, [id]);

    const loadRide = async () => {
        setLoading(true);
        try {
            const res = await api.get(`/rides/${id}`);
            setRide(res.data);
        } catch (err) {
            console.log('Failed to load ride detail:', err);
        }
        setLoading(false);
    };

    const hasPickup = ride?.pickup_lat && ride?.pickup_lng;
    const hasDropoff = ride?.dropoff_lat && ride?.dropoff_lng;

    const mapRegion = useMemo(() => {
        if (!ride) return { latitude: 52.1332, longitude: -106.67, latitudeDelta: 0.03, longitudeDelta: 0.03 };
        if (hasPickup && hasDropoff) {
            const minLat = Math.min(ride.pickup_lat, ride.dropoff_lat);
            const maxLat = Math.max(ride.pickup_lat, ride.dropoff_lat);
            const minLng = Math.min(ride.pickup_lng, ride.dropoff_lng);
            const maxLng = Math.max(ride.pickup_lng, ride.dropoff_lng);
            const padLat = Math.max((maxLat - minLat) * 0.4, 0.01);
            const padLng = Math.max((maxLng - minLng) * 0.4, 0.01);
            return {
                latitude: (minLat + maxLat) / 2,
                longitude: (minLng + maxLng) / 2,
                latitudeDelta: (maxLat - minLat) + padLat,
                longitudeDelta: (maxLng - minLng) + padLng,
            };
        }
        return {
            latitude: ride.pickup_lat || 52.1332,
            longitude: ride.pickup_lng || -106.6700,
            latitudeDelta: 0.03,
            longitudeDelta: 0.03,
        };
    }, [ride, hasPickup, hasDropoff]);

    const routeCoords = useMemo(() => {
        if (!ride) return [];
        const coords: { latitude: number; longitude: number }[] = [];
        if (hasPickup) coords.push({ latitude: ride.pickup_lat, longitude: ride.pickup_lng });
        if (hasDropoff) coords.push({ latitude: ride.dropoff_lat, longitude: ride.dropoff_lng });
        return coords;
    }, [ride, hasPickup, hasDropoff]);

    const savedPolyline = useMemo(() => {
        if (!ride) return [];
        // Actual GPS path (completed rides) → planned route (all rides) → empty
        const raw = (Array.isArray(ride.route_polyline) && ride.route_polyline.length >= 2)
            ? ride.route_polyline
            : ride.planned_route_polyline;
        if (!Array.isArray(raw) || raw.length < 2) return [];
        return raw
            .filter((p: any) => Array.isArray(p) && p.length >= 2)
            .map((p: any) => ({ latitude: p[0], longitude: p[1] }));
    }, [ride]);

    const polylineToRender = savedPolyline.length >= 2 ? savedPolyline : fallbackCoords;

    if (loading) {
        return (
            <View style={[styles.container, { justifyContent: 'center', alignItems: 'center' }]}>
                <ActivityIndicator color={colors.primary} size="large" />
            </View>
        );
    }

    if (!ride) {
        return (
            <View style={[styles.container, { justifyContent: 'center', alignItems: 'center' }]}>
                <Ionicons name="alert-circle" size={48} color={colors.primary} />
                <Text style={styles.errorText}>Ride not found</Text>
                <TouchableOpacity onPress={() => router.back()} style={styles.backLink}>
                    <Text style={styles.backLinkText}>Go Back</Text>
                </TouchableOpacity>
            </View>
        );
    }

    const isCompleted = ride.status === 'completed';
    const statusColor = isCompleted ? colors.primary : colors.primary;
    const statusLabel = ride.status?.charAt(0).toUpperCase() + ride.status?.slice(1);

    const completedDate = ride.ride_completed_at || ride.cancelled_at || ride.created_at;
    const formattedDate = completedDate
        ? new Date(completedDate).toLocaleDateString('en', {
            weekday: 'long',
            month: 'long',
            day: 'numeric',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        })
        : '';

    return (
        <View style={styles.container}>
            <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: insets.bottom + 24 }}>
                {/* Route Map — snapshot image first, live MapView as fallback */}
                {hasPickup && hasDropoff && (
                    <View style={styles.mapContainer}>
                        {ride.route_snapshot_url ? (
                            <Image
                                source={{ uri: ride.route_snapshot_url }}
                                style={styles.map}
                                resizeMode="cover"
                            />
                        ) : (
                            <MapView
                                ref={mapRef}
                                style={styles.map}
                                provider={MAP_PROVIDER}
                                initialRegion={mapRegion}
                                scrollEnabled={false}
                                zoomEnabled={false}
                                rotateEnabled={false}
                                onMapReady={() => {
                                    if (savedPolyline.length >= 2) {
                                        mapRef.current?.fitToCoordinates(savedPolyline, {
                                            edgePadding: { top: 40, right: 40, bottom: 40, left: 40 },
                                            animated: false,
                                        });
                                    }
                                }}
                            >
                                {savedPolyline.length < 2 && GOOGLE_MAPS_API_KEY && (
                                    <MapViewDirections
                                        origin={{ latitude: ride.pickup_lat, longitude: ride.pickup_lng }}
                                        destination={{ latitude: ride.dropoff_lat, longitude: ride.dropoff_lng }}
                                        apikey={GOOGLE_MAPS_API_KEY}
                                        strokeWidth={0}
                                        strokeColor="transparent"
                                        onReady={(r: any) => {
                                            setFallbackCoords(r.coordinates);
                                            mapRef.current?.fitToCoordinates(r.coordinates, {
                                                edgePadding: { top: 40, right: 40, bottom: 40, left: 40 },
                                                animated: false,
                                            });
                                        }}
                                    />
                                )}
                                {polylineToRender.length > 1 && (() => {
                                    const total = polylineToRender.length;
                                    const SEGS = 15;
                                    const chunk = Math.max(1, Math.floor(total / SEGS));
                                    const segments: { coords: any[]; color: string }[] = [];
                                    for (let i = 0; i < total - 1; i += chunk) {
                                        const end = Math.min(i + chunk + 1, total);
                                        const t = i / Math.max(total - 1, 1);
                                        const r = Math.round(255 + (238 - 255) * t);
                                        const g = Math.round(149 + (43 - 149) * t);
                                        const b = Math.round(0 + (43 - 0) * t);
                                        segments.push({ coords: polylineToRender.slice(i, end), color: `rgb(${r},${g},${b})` });
                                    }
                                    return segments.map((seg, idx) => (
                                        <Polyline
                                            key={`seg-${idx}`}
                                            coordinates={seg.coords}
                                            strokeWidth={4}
                                            strokeColor={seg.color}
                                            lineCap="round"
                                            lineJoin="round"
                                        />
                                    ));
                                })()}
                                <Marker coordinate={{ latitude: ride.pickup_lat, longitude: ride.pickup_lng }} anchor={{ x: 0.5, y: 0.5 }}>
                                    <View style={[styles.markerDot, { backgroundColor: '#10B981' }]}>
                                        <Ionicons name="location" size={14} color="#fff" />
                                    </View>
                                </Marker>
                                <Marker coordinate={{ latitude: ride.dropoff_lat, longitude: ride.dropoff_lng }} anchor={{ x: 0.5, y: 0.5 }}>
                                    <View style={[styles.markerDot, { backgroundColor: '#EF4444' }]}>
                                        <Ionicons name="flag" size={14} color="#fff" />
                                    </View>
                                </Marker>
                            </MapView>
                        )}

                        <TouchableOpacity style={[styles.backBtn, { top: insets.top + 12 }]} onPress={() => router.back()}>
                            <Ionicons name="arrow-back" size={22} color="#fff" />
                        </TouchableOpacity>
                    </View>
                )}

                {/* Status + Date */}
                <View style={styles.content}>
                    <View style={styles.statusRow}>
                        <View style={[styles.statusBadge, { backgroundColor: `${statusColor}15` }]}>
                            <View style={[styles.statusDot, { backgroundColor: statusColor }]} />
                            <Text style={[styles.statusText, { color: statusColor }]}>{statusLabel}</Text>
                        </View>
                        <Text style={styles.dateText}>{formattedDate}</Text>
                    </View>

                    {/* Route */}
                    <View style={styles.card}>
                        <View style={styles.routeRow}>
                            <View style={styles.routeDots}>
                                <View style={[styles.dot, { backgroundColor: colors.primary }]} />
                                <View style={styles.dotLine} />
                                <View style={[styles.dot, { backgroundColor: colors.primary }]} />
                            </View>
                            <View style={styles.routeTexts}>
                                <View>
                                    <Text style={styles.routeLabel}>PICKUP</Text>
                                    <Text style={styles.routeAddress}>{ride.pickup_address || 'Not available'}</Text>
                                </View>
                                <View>
                                    <Text style={styles.routeLabel}>DROPOFF</Text>
                                    <Text style={styles.routeAddress}>{ride.dropoff_address || 'Not available'}</Text>
                                </View>
                            </View>
                        </View>
                    </View>

                    {/* Trip Stats */}
                    <View style={styles.statsRow}>
                        <View style={styles.stat}>
                            <Ionicons name="speedometer" size={20} color={colors.primary} />
                            <Text style={styles.statValue}>{(ride.distance_km || 0).toFixed(1)} km</Text>
                            <Text style={styles.statLabel}>Distance</Text>
                        </View>
                        <View style={styles.statDivider} />
                        <View style={styles.stat}>
                            <Ionicons name="time" size={20} color="#FF9500" />
                            <Text style={styles.statValue}>
                                {ride.ride_started_at && ride.ride_completed_at
                                    ? Math.round((new Date(ride.ride_completed_at).getTime() - new Date(ride.ride_started_at).getTime()) / 60000)
                                    : (ride.duration_minutes || 0)} min
                            </Text>
                            <Text style={styles.statLabel}>Duration</Text>
                        </View>
                        <View style={styles.statDivider} />
                        <View style={styles.stat}>
                            <Ionicons name="star" size={20} color="#FFD700" />
                            <Text style={styles.statValue}>
                                {ride.rider_rating !== null && ride.rider_rating !== undefined
                                    ? ride.rider_rating
                                    : '—'}
                            </Text>
                            <Text style={styles.statLabel}>Rating</Text>
                        </View>
                    </View>

                    {/* Trip Earnings Breakdown */}
                    {isCompleted && (
                        <>
                            {/* Rider name (first name only — PIPEDA) */}
                            {ride.rider_name && (
                                <View style={styles.riderRow}>
                                    <Ionicons name="person-circle-outline" size={18} color={colors.textDim} />
                                    <Text style={styles.riderNameText}>
                                        {ride.rider_name.trim().split(' ')[0]}
                                    </Text>
                                </View>
                            )}

                            <View style={styles.card}>
                                <Text style={styles.cardTitle}>Your Earnings</Text>

                                {/* Ride Fare = exactly what the rider was charged for the trip
                                    (base + distance + time, all locked at booking). This single
                                    line matches the "Ride fare" the rider sees on their receipt
                                    so the driver can verify they received 100% of it. */}
                                {(() => {
                                    const rideFare = parseFloat(ride.base_fare || '0')
                                        + parseFloat(ride.distance_fare || '0')
                                        + parseFloat(ride.time_fare || '0');
                                    const distKm = parseFloat(ride.distance_km || '0').toFixed(1);
                                    const surge = parseFloat(ride.surge_multiplier || '1');
                                    return (
                                        <>
                                            <View style={styles.fareRow}>
                                                <Text style={styles.fareLabel}>
                                                    Ride Fare ({distKm} km){surge > 1 ? ` · ${surge.toFixed(1)}× surge` : ''}
                                                </Text>
                                                <Text style={styles.fareValue}>
                                                    ${rideFare.toFixed(2)}
                                                </Text>
                                            </View>
                                        </>
                                    );
                                })()}

                                {parseFloat(ride.tip_amount || '0') > 0 && (
                                    <View style={styles.fareRow}>
                                        <Text style={[styles.fareLabel, { color: '#f59e0b' }]}>Tip</Text>
                                        <Text style={[styles.fareValue, { color: '#f59e0b' }]}>
                                            +${parseFloat(ride.tip_amount || '0').toFixed(2)}
                                        </Text>
                                    </View>
                                )}

                                {parseFloat(ride.incentive_amount || '0') > 0 && (
                                    <View style={styles.fareRow}>
                                        <Text style={[styles.fareLabel, { color: '#8b5cf6' }]}>Area Boost</Text>
                                        <Text style={[styles.fareValue, { color: '#8b5cf6' }]}>
                                            +${parseFloat(ride.incentive_amount || '0').toFixed(2)}
                                        </Text>
                                    </View>
                                )}

                                {parseFloat(ride.tax_amount || '0') > 0 && (
                                    <View style={styles.fareRow}>
                                        <Text style={[styles.fareLabel, { color: '#6366f1' }]}>Tax</Text>
                                        <Text style={[styles.fareValue, { color: '#6366f1' }]}>
                                            +${parseFloat(ride.tax_amount || '0').toFixed(2)}
                                        </Text>
                                    </View>
                                )}

                                <View style={styles.fareDivider} />

                                <View style={styles.fareRow}>
                                    <Text style={styles.earningsLabel}>Total Earned</Text>
                                    <Text style={styles.earningsValue}>
                                        ${parseFloat(ride.total_earned || ride.driver_earnings || '0').toFixed(2)}
                                    </Text>
                                </View>
                            </View>

                            {parseFloat(ride.tax_amount || '0') > 0 && (
                                <View style={styles.card}>
                                    <Text style={styles.cardTitle}>Tax Info</Text>

                                    <View style={styles.fareRow}>
                                        <Text style={styles.fareLabel}>GST (5%)</Text>
                                        <Text style={styles.fareValue}>
                                            ${parseFloat(ride.tax_breakdown?.GST?.amount ?? '0').toFixed(2)}
                                        </Text>
                                    </View>
                                    <View style={styles.fareRow}>
                                        <Text style={styles.fareLabel}>PST (6%)</Text>
                                        <Text style={styles.fareValue}>
                                            ${parseFloat(ride.tax_breakdown?.PST?.amount ?? '0').toFixed(2)}
                                        </Text>
                                    </View>

                                    <View style={styles.fareDivider} />
                                    <View style={styles.taxNoteBox}>
                                        <Ionicons name="information-circle-outline" size={16} color={colors.textDim} />
                                        <Text style={styles.taxNoteText}>
                                            Tax collected from the rider is included in your total earnings. You are responsible for filing and remitting this amount.
                                        </Text>
                                    </View>
                                </View>
                            )}
                        </>
                    )}

                    {/* Cancellation / No-show fee earned */}
                    {ride.status === 'cancelled' && parseFloat(ride.cancel_fee_earned || '0') > 0 && (
                        <View style={styles.card}>
                            <Text style={styles.cardTitle}>
                                {ride.cancellation_type === 'noshow' ? 'No-Show Fee' : 'Cancellation Fee'}
                            </Text>
                            <View style={styles.fareRow}>
                                <Text style={styles.fareLabel}>
                                    {ride.cancellation_type === 'noshow'
                                        ? 'Rider did not show up'
                                        : 'Rider cancelled after acceptance'}
                                </Text>
                                <Text style={[styles.fareValue, { color: '#f59e0b' }]}>
                                    +${parseFloat(ride.cancel_fee_earned || '0').toFixed(2)}
                                </Text>
                            </View>
                            <View style={styles.fareDivider} />
                            <View style={styles.taxNoteBox}>
                                <Ionicons name="information-circle-outline" size={16} color={colors.textDim} />
                                <Text style={styles.taxNoteText}>
                                    This fee has been credited to your wallet.
                                </Text>
                            </View>
                        </View>
                    )}

                    {/* Trip Timeline */}
                    <View style={styles.card}>
                        <Text style={styles.cardTitle}>Trip Timeline</Text>
                        {[
                            { label: 'Ride Created', time: ride.created_at, icon: 'add-circle' },
                            { label: 'Driver Accepted', time: ride.driver_accepted_at, icon: 'checkmark-circle' },
                            { label: 'Driver Arrived', time: ride.driver_arrived_at, icon: 'navigate-circle' },
                            { label: 'Ride Started', time: ride.ride_started_at, icon: 'play-circle' },
                            { label: 'Ride Completed', time: ride.ride_completed_at, icon: 'checkmark-done-circle' },
                            { label: 'Cancelled', time: ride.cancelled_at, icon: 'close-circle' },
                        ]
                            .filter((e) => e.time)
                            .map((event, i) => (
                                <View key={i} style={styles.timelineRow}>
                                    <Ionicons name={event.icon as any} size={20} color={colors.primary} />
                                    <View style={{ flex: 1 }}>
                                        <Text style={styles.timelineLabel}>{event.label}</Text>
                                        <Text style={styles.timelineTime}>
                                            {new Date(event.time).toLocaleTimeString('en', {
                                                hour: '2-digit',
                                                minute: '2-digit',
                                                second: '2-digit',
                                            })}
                                        </Text>
                                    </View>
                                </View>
                            ))}
                    </View>
                </View>
            </ScrollView>
        </View>
    );
}

function createStyles(colors: ThemeColors) {
    return StyleSheet.create({
        container: { flex: 1, backgroundColor: colors.background },
        mapContainer: { height: 220, position: 'relative' },
        map: { ...StyleSheet.absoluteFill },
        backBtn: {
            position: 'absolute',
            left: 16,
            width: 40,
            height: 40,
            borderRadius: 20,
            backgroundColor: 'rgba(10, 14, 33, 0.85)',
            justifyContent: 'center',
            alignItems: 'center',
        },
        markerDot: {
            width: 28,
            height: 28,
            borderRadius: 14,
            justifyContent: 'center',
            alignItems: 'center',
            borderWidth: 2,
            borderColor: '#fff',
        },
        content: { padding: 16 },
        statusRow: {
            flexDirection: 'row',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: 16,
        },
        statusBadge: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 6,
            paddingHorizontal: 12,
            paddingVertical: 6,
            borderRadius: 14,
        },
        statusDot: { width: 7, height: 7, borderRadius: 4 },
        statusText: { fontSize: 13, fontWeight: '700' },
        dateText: { color: colors.textDim, fontSize: 12 },
        card: {
            backgroundColor: colors.surface,
            borderRadius: 18,
            padding: 16,
            marginBottom: 14,
            borderWidth: 1,
            borderColor: colors.border,
        },
        cardTitle: {
            color: colors.text,
            fontSize: 16,
            fontWeight: '700',
            marginBottom: 14,
        },
        routeRow: { flexDirection: 'row', gap: 12 },
        routeDots: { alignItems: 'center', width: 12, paddingTop: 6 },
        dot: { width: 10, height: 10, borderRadius: 5 },
        dotLine: {
            width: 2,
            height: 30,
            backgroundColor: colors.surfaceLight,
            marginVertical: 4,
        },
        routeTexts: { flex: 1, gap: 18 },
        routeLabel: {
            color: colors.textDim,
            fontSize: 10,
            letterSpacing: 1,
            fontWeight: '600',
            marginBottom: 2,
        },
        routeAddress: { color: colors.text, fontSize: 14, fontWeight: '500' },
        statsRow: {
            flexDirection: 'row',
            backgroundColor: colors.surface,
            borderRadius: 18,
            padding: 16,
            marginBottom: 14,
            borderWidth: 1,
            borderColor: colors.border,
        },
        stat: { flex: 1, alignItems: 'center', gap: 6 },
        statDivider: { width: 1, backgroundColor: colors.surfaceLight },
        statValue: { color: colors.text, fontSize: 18, fontWeight: '800' },
        statLabel: { color: colors.textDim, fontSize: 11 },
        fareRow: {
            flexDirection: 'row',
            justifyContent: 'space-between',
            marginBottom: 8,
        },
        fareLabel: { color: colors.textDim, fontSize: 14 },
        fareValue: { color: colors.text, fontSize: 14, fontWeight: '600' },
        fareDivider: {
            height: 1,
            backgroundColor: colors.surfaceLight,
            marginVertical: 8,
        },
        earningsLabel: { color: colors.primary, fontSize: 16, fontWeight: '700' },
        earningsValue: { color: colors.primary, fontSize: 20, fontWeight: '800' },
        riderRow: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 8,
            marginBottom: 12,
        },
        riderNameText: {
            color: colors.textDim,
            fontSize: 14,
            fontWeight: '600',
        },
        taxNoteBox: {
            flexDirection: 'row',
            alignItems: 'flex-start',
            gap: 8,
            backgroundColor: colors.surfaceLight,
            borderRadius: 10,
            padding: 12,
        },
        taxNoteText: {
            flex: 1,
            color: colors.textDim,
            fontSize: 12,
            lineHeight: 18,
        },
        timelineRow: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 12,
            marginBottom: 12,
        },
        timelineLabel: { color: colors.text, fontSize: 14, fontWeight: '500' },
        timelineTime: { color: colors.textDim, fontSize: 12, marginTop: 2 },
        errorText: { color: colors.textDim, fontSize: 16, marginTop: 12 },
        backLink: { marginTop: 16, padding: 10 },
        backLinkText: { color: colors.primary, fontSize: 15, fontWeight: '600' },
    });
}
