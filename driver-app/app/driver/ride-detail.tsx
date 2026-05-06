import React, { useEffect, useState, useMemo } from 'react';
import {
    View,
    Text,
    StyleSheet,
    ScrollView,
    Platform,
    ActivityIndicator,
    TouchableOpacity,
    Dimensions,
} from 'react-native';
import MapView, { Marker, Polyline, PROVIDER_GOOGLE } from 'react-native-maps';

// Use Google Maps on Android, Apple Maps (native) on iOS
const MAP_PROVIDER = Platform.OS === 'android' ? PROVIDER_GOOGLE : undefined;
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useLocalSearchParams, useRouter } from 'expo-router';
import api from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

const { width } = Dimensions.get('window');

// Use standard map style instead of dark style for consistency with white theme
const mapStyle: any[] = [];

export default function RideDetailScreen() {
    const { id } = useLocalSearchParams<{ id: string }>();
    const router = useRouter();
    const [ride, setRide] = useState<any>(null);
    const [loading, setLoading] = useState(true);
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

    const mapRegion = {
        latitude: ride.pickup_lat || 52.1332,
        longitude: ride.pickup_lng || -106.6700,
        latitudeDelta: 0.06,
        longitudeDelta: 0.06,
    };

    // Build route coordinates for polyline
    const routeCoords = [];
    if (ride.pickup_lat && ride.pickup_lng) {
        routeCoords.push({ latitude: ride.pickup_lat, longitude: ride.pickup_lng });
    }
    if (ride.dropoff_lat && ride.dropoff_lng) {
        routeCoords.push({ latitude: ride.dropoff_lat, longitude: ride.dropoff_lng });
    }

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
            <ScrollView showsVerticalScrollIndicator={false}>
                {/* Back Button + Map */}
                <View style={styles.mapContainer}>
                    <MapView
                        style={styles.map}
                        provider={MAP_PROVIDER}
                        initialRegion={mapRegion}
                        customMapStyle={mapStyle}
                        scrollEnabled={false}
                        zoomEnabled={false}
                    >
                        {ride.pickup_lat && ride.pickup_lng && (
                            <Marker
                                coordinate={{ latitude: ride.pickup_lat, longitude: ride.pickup_lng }}
                                title="Pickup"
                            >
                                <View style={[styles.markerDot, { backgroundColor: '#10B981' }]}>
                                    <Ionicons name="location" size={14} color="#fff" />
                                </View>
                            </Marker>
                        )}
                        {ride.dropoff_lat && ride.dropoff_lng && (
                            <Marker
                                coordinate={{ latitude: ride.dropoff_lat, longitude: ride.dropoff_lng }}
                                title="Dropoff"
                            >
                                <View style={[styles.markerDot, { backgroundColor: '#EF4444' }]}>
                                    <Ionicons name="flag" size={14} color="#fff" />
                                </View>
                            </Marker>
                        )}
                        {routeCoords.length === 2 && (
                            <Polyline
                                coordinates={routeCoords}
                                strokeColor="#EE2B2B"
                                strokeWidth={3}
                                lineDashPattern={[6, 4]}
                            />
                        )}
                    </MapView>

                    <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
                        <Ionicons name="arrow-back" size={22} color={colors.text} />
                    </TouchableOpacity>
                </View>

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
                                    <Text style={styles.routeAddress}>{ride.pickup_address || 'Pickup location'}</Text>
                                </View>
                                <View>
                                    <Text style={styles.routeLabel}>DROPOFF</Text>
                                    <Text style={styles.routeAddress}>{ride.dropoff_address || 'Dropoff location'}</Text>
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
                            <Text style={styles.statValue}>{ride.duration_minutes || 0} min</Text>
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
                                <Text style={styles.cardTitle}>Trip Earnings</Text>

                                {/* Base fare — always shown */}
                                <View style={styles.fareRow}>
                                    <Text style={styles.fareLabel}>Base Fare</Text>
                                    <Text style={styles.fareValue}>
                                        ${parseFloat(ride.base_fare || '0').toFixed(2)}
                                    </Text>
                                </View>

                                {/* Distance charge — hidden if zero */}
                                {parseFloat(ride.distance_fare || '0') > 0 && (
                                    <View style={styles.fareRow}>
                                        <Text style={styles.fareLabel}>Distance Charge</Text>
                                        <Text style={styles.fareValue}>
                                            ${parseFloat(ride.distance_fare || '0').toFixed(2)}
                                        </Text>
                                    </View>
                                )}

                                {/* Time charge — hidden if zero */}
                                {parseFloat(ride.time_fare || '0') > 0 && (
                                    <View style={styles.fareRow}>
                                        <Text style={styles.fareLabel}>Time Charge</Text>
                                        <Text style={styles.fareValue}>
                                            ${parseFloat(ride.time_fare || '0').toFixed(2)}
                                        </Text>
                                    </View>
                                )}

                                {/* Booking fee — informational */}
                                {parseFloat(ride.booking_fee || '0') > 0 && (
                                    <View style={styles.fareRow}>
                                        <Text style={styles.fareLabel}>Booking Fee</Text>
                                        <Text style={styles.fareValue}>
                                            ${parseFloat(ride.booking_fee || '0').toFixed(2)}
                                        </Text>
                                    </View>
                                )}

                                {/* Surge bonus — hidden when multiplier is 1.0 */}
                                {parseFloat(ride.surge_multiplier || '1') > 1 && (
                                    <View style={styles.fareRow}>
                                        <Text style={styles.fareLabel}>
                                            Surge Bonus ({parseFloat(ride.surge_multiplier || '1').toFixed(2)}×)
                                        </Text>
                                        <Text style={[styles.fareValue, { color: '#FF9500' }]}>
                                            ${parseFloat(ride.surge_fee || '0').toFixed(2)}
                                        </Text>
                                    </View>
                                )}

                                {/* Tip — shown in gold when present */}
                                {parseFloat(ride.tip_amount || '0') > 0 && (
                                    <View style={styles.fareRow}>
                                        <Text style={[styles.fareLabel, { color: '#FFD700' }]}>Tip</Text>
                                        <Text style={[styles.fareValue, { color: '#FFD700' }]}>
                                            +${parseFloat(ride.tip_amount || '0').toFixed(2)}
                                        </Text>
                                    </View>
                                )}

                                <View style={styles.fareDivider} />

                                {/* Your Earnings = grand_total (0% commission model) */}
                                <View style={styles.fareRow}>
                                    <Text style={styles.earningsLabel}>Your Earnings</Text>
                                    <Text style={styles.earningsValue}>
                                        ${parseFloat(ride.grand_total || ride.total_fare || '0').toFixed(2)}
                                    </Text>
                                </View>
                            </View>

                            {/* T4A Tax Summary — informational only */}
                            <View style={styles.card}>
                                <Text style={styles.cardTitle}>Tax Summary (T4A)</Text>

                                {/* GST: 5% of subtotal before taxes */}
                                {(() => {
                                    const subtotal = parseFloat(ride.subtotal || ride.fare_before_tax || '0');
                                    const gstAmt = subtotal > 0
                                        ? subtotal * 0.05
                                        : parseFloat(
                                            (ride.tax_breakdown?.GST?.amount ?? ride.gst_amount ?? '0').toString()
                                        );
                                    const pstAmt = subtotal > 0
                                        ? subtotal * 0.06
                                        : parseFloat(
                                            (ride.tax_breakdown?.PST?.amount ?? ride.pst_amount ?? '0').toString()
                                        );
                                    return (
                                        <>
                                            <View style={styles.fareRow}>
                                                <Text style={styles.fareLabel}>GST Collected (5%)</Text>
                                                <Text style={styles.fareValue}>${gstAmt.toFixed(2)}</Text>
                                            </View>
                                            <View style={styles.fareRow}>
                                                <Text style={styles.fareLabel}>PST Collected (6%)</Text>
                                                <Text style={styles.fareValue}>${pstAmt.toFixed(2)}</Text>
                                            </View>
                                        </>
                                    );
                                })()}

                                <View style={styles.fareDivider} />
                                <View style={[styles.taxNoteBox]}>
                                    <Ionicons name="information-circle-outline" size={16} color={colors.textDim} />
                                    <Text style={styles.taxNoteText}>
                                        GST/PST collected from rider and remitted. Not deducted from your earnings.
                                    </Text>
                                </View>
                            </View>
                        </>
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
        mapContainer: { height: 250, position: 'relative' },
        map: { ...StyleSheet.absoluteFillObject },
        backBtn: {
            position: 'absolute',
            top: Platform.OS === 'ios' ? 50 : 35,
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
