import React, { useEffect, useState } from 'react';
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
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useLocalSearchParams, useRouter } from 'expo-router';
import api from '../../api/client';

const { width } = Dimensions.get('window');

const COLORS = {
    primary: '#FF3B30', // Vibrant Red
    accent: '#FF3B30',
    accentDim: '#D32F2F',
    surface: '#FFFFFF',
    surfaceLight: '#F5F5F5',
    text: '#1A1A1A',
    textDim: '#666666',
    success: '#34C759',
    gold: '#FFD700',
    orange: '#FF9500',
    danger: '#DC2626',
    border: '#E5E7EB',
};

// Use standard map style instead of dark style for consistency with white theme
const mapStyle: any[] = [];

export default function RideDetailScreen() {
    const { id } = useLocalSearchParams<{ id: string }>();
    const router = useRouter();
    const [ride, setRide] = useState<any>(null);
    const [loading, setLoading] = useState(true);

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
                <ActivityIndicator color={COLORS.accent} size="large" />
            </View>
        );
    }

    if (!ride) {
        return (
            <View style={[styles.container, { justifyContent: 'center', alignItems: 'center' }]}>
                <Ionicons name="alert-circle" size={48} color={COLORS.danger} />
                <Text style={styles.errorText}>Ride not found</Text>
                <TouchableOpacity onPress={() => router.back()} style={styles.backLink}>
                    <Text style={styles.backLinkText}>Go Back</Text>
                </TouchableOpacity>
            </View>
        );
    }

    const isCompleted = ride.status === 'completed';
    const statusColor = isCompleted ? COLORS.accent : COLORS.danger;
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
                        provider={PROVIDER_GOOGLE}
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
                                <View style={[styles.markerDot, { backgroundColor: COLORS.accent }]}>
                                    <Ionicons name="location" size={14} color="#fff" />
                                </View>
                            </Marker>
                        )}
                        {ride.dropoff_lat && ride.dropoff_lng && (
                            <Marker
                                coordinate={{ latitude: ride.dropoff_lat, longitude: ride.dropoff_lng }}
                                title="Dropoff"
                            >
                                <View style={[styles.markerDot, { backgroundColor: COLORS.danger }]}>
                                    <Ionicons name="flag" size={14} color="#fff" />
                                </View>
                            </Marker>
                        )}
                        {routeCoords.length === 2 && (
                            <Polyline
                                coordinates={routeCoords}
                                strokeColor={COLORS.accent}
                                strokeWidth={3}
                                lineDashPattern={[6, 4]}
                            />
                        )}
                    </MapView>

                    <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
                        <Ionicons name="arrow-back" size={22} color={COLORS.text} />
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
                                <View style={[styles.dot, { backgroundColor: COLORS.accent }]} />
                                <View style={styles.dotLine} />
                                <View style={[styles.dot, { backgroundColor: COLORS.danger }]} />
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
                            <Ionicons name="speedometer" size={20} color={COLORS.accent} />
                            <Text style={styles.statValue}>{(ride.distance_km || 0).toFixed(1)} km</Text>
                            <Text style={styles.statLabel}>Distance</Text>
                        </View>
                        <View style={styles.statDivider} />
                        <View style={styles.stat}>
                            <Ionicons name="time" size={20} color={COLORS.orange} />
                            <Text style={styles.statValue}>{ride.duration_minutes || 0} min</Text>
                            <Text style={styles.statLabel}>Duration</Text>
                        </View>
                        <View style={styles.statDivider} />
                        <View style={styles.stat}>
                            <Ionicons name="star" size={20} color={COLORS.gold} />
                            <Text style={styles.statValue}>
                                {ride.rider_rating !== null && ride.rider_rating !== undefined
                                    ? ride.rider_rating
                                    : '—'}
                            </Text>
                            <Text style={styles.statLabel}>Rating</Text>
                        </View>
                    </View>

                    {/* Fare Breakdown */}
                    {isCompleted && (
                        <View style={styles.card}>
                            <Text style={styles.cardTitle}>Fare Breakdown</Text>
                            <View style={styles.fareRow}>
                                <Text style={styles.fareLabel}>Base Fare</Text>
                                <Text style={styles.fareValue}>${(ride.base_fare || 0).toFixed(2)}</Text>
                            </View>
                            <View style={styles.fareRow}>
                                <Text style={styles.fareLabel}>Distance Fare</Text>
                                <Text style={styles.fareValue}>${(ride.distance_fare || 0).toFixed(2)}</Text>
                            </View>
                            <View style={styles.fareRow}>
                                <Text style={styles.fareLabel}>Time Fare</Text>
                                <Text style={styles.fareValue}>${(ride.time_fare || 0).toFixed(2)}</Text>
                            </View>
                            {ride.surge_multiplier > 1 && (
                                <View style={styles.fareRow}>
                                    <Text style={styles.fareLabel}>Surge ({ride.surge_multiplier}x)</Text>
                                    <Text style={[styles.fareValue, { color: COLORS.orange }]}>
                                        ${(ride.surge_fee || 0).toFixed(2)}
                                    </Text>
                                </View>
                            )}
                            <View style={styles.fareDivider} />
                            <View style={styles.fareRow}>
                                <Text style={styles.fareLabel}>Total Fare</Text>
                                <Text style={[styles.fareValue, { fontSize: 16 }]}>
                                    ${(ride.total_fare || 0).toFixed(2)}
                                </Text>
                            </View>
                            <View style={styles.fareRow}>
                                <Text style={styles.fareLabel}>Platform Fee</Text>
                                <Text style={[styles.fareValue, { color: COLORS.danger }]}>
                                    -${(ride.platform_fee || 0).toFixed(2)}
                                </Text>
                            </View>
                            {(ride.tip_amount || 0) > 0 && (
                                <View style={styles.fareRow}>
                                    <Text style={[styles.fareLabel, { color: COLORS.gold }]}>Tip</Text>
                                    <Text style={[styles.fareValue, { color: COLORS.gold }]}>
                                        +${ride.tip_amount.toFixed(2)}
                                    </Text>
                                </View>
                            )}
                            <View style={styles.fareDivider} />
                            <View style={styles.fareRow}>
                                <Text style={styles.earningsLabel}>Your Earnings</Text>
                                <Text style={styles.earningsValue}>
                                    ${(ride.driver_earnings || 0).toFixed(2)}
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
                                    <Ionicons name={event.icon as any} size={20} color={COLORS.accent} />
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

const styles = StyleSheet.create({
    container: { flex: 1, backgroundColor: COLORS.primary },
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
    dateText: { color: COLORS.textDim, fontSize: 12 },
    card: {
        backgroundColor: COLORS.surface,
        borderRadius: 18,
        padding: 16,
        marginBottom: 14,
        borderWidth: 1,
        borderColor: 'rgba(255,255,255,0.04)',
    },
    cardTitle: {
        color: COLORS.text,
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
        backgroundColor: COLORS.surfaceLight,
        marginVertical: 4,
    },
    routeTexts: { flex: 1, gap: 18 },
    routeLabel: {
        color: COLORS.textDim,
        fontSize: 10,
        letterSpacing: 1,
        fontWeight: '600',
        marginBottom: 2,
    },
    routeAddress: { color: COLORS.text, fontSize: 14, fontWeight: '500' },
    statsRow: {
        flexDirection: 'row',
        backgroundColor: COLORS.surface,
        borderRadius: 18,
        padding: 16,
        marginBottom: 14,
        borderWidth: 1,
        borderColor: 'rgba(255,255,255,0.04)',
    },
    stat: { flex: 1, alignItems: 'center', gap: 6 },
    statDivider: { width: 1, backgroundColor: COLORS.surfaceLight },
    statValue: { color: COLORS.text, fontSize: 18, fontWeight: '800' },
    statLabel: { color: COLORS.textDim, fontSize: 11 },
    fareRow: {
        flexDirection: 'row',
        justifyContent: 'space-between',
        marginBottom: 8,
    },
    fareLabel: { color: COLORS.textDim, fontSize: 14 },
    fareValue: { color: COLORS.text, fontSize: 14, fontWeight: '600' },
    fareDivider: {
        height: 1,
        backgroundColor: COLORS.surfaceLight,
        marginVertical: 8,
    },
    earningsLabel: { color: COLORS.accent, fontSize: 16, fontWeight: '700' },
    earningsValue: { color: COLORS.accent, fontSize: 20, fontWeight: '800' },
    timelineRow: {
        flexDirection: 'row',
        alignItems: 'center',
        gap: 12,
        marginBottom: 12,
    },
    timelineLabel: { color: COLORS.text, fontSize: 14, fontWeight: '500' },
    timelineTime: { color: COLORS.textDim, fontSize: 12, marginTop: 2 },
    errorText: { color: COLORS.textDim, fontSize: 16, marginTop: 12 },
    backLink: { marginTop: 16, padding: 10 },
    backLinkText: { color: COLORS.accent, fontSize: 15, fontWeight: '600' },
});
