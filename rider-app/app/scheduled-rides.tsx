import React, { useEffect, useState, useMemo } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, FlatList,
  ActivityIndicator, RefreshControl,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useRideStore } from '../store/rideStore';
import CustomAlert from '@shared/components/CustomAlert';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

export default function ScheduledRidesScreen() {
  const router = useRouter();
  const { scheduledRides, fetchScheduledRides, cancelScheduledRide } = useRideStore();
  const { colors, isDark } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [alertState, setAlertState] = useState<{
    visible: boolean; title: string; message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: Array<{ text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }>;
  }>({ visible: false, title: '', message: '', variant: 'info' });

  useEffect(() => {
    loadRides();
  }, []);

  const loadRides = async () => {
    setLoading(true);
    await fetchScheduledRides();
    setLoading(false);
  };

  const onRefresh = async () => {
    setRefreshing(true);
    await fetchScheduledRides();
    setRefreshing(false);
  };

  const handleCancel = (rideId: string) => {
    setAlertState({
      visible: true,
      title: 'Cancel Scheduled Ride',
      message: 'Are you sure you want to cancel this scheduled ride?',
      variant: 'warning',
      buttons: [
        { text: 'Keep', style: 'cancel' },
        {
          text: 'Cancel Ride', style: 'destructive',
          onPress: async () => {
            setAlertState(prev => ({ ...prev, visible: false }));
            try {
              await cancelScheduledRide(rideId);
              setAlertState({
                visible: true, title: 'Cancelled',
                message: 'Your scheduled ride has been cancelled.',
                variant: 'success',
              });
            } catch (err: any) {
              setAlertState({
                visible: true, title: 'Error',
                message: err.message || 'Failed to cancel ride',
                variant: 'danger',
              });
            }
          },
        },
      ],
    });
  };

  const formatDateTime = (dateStr: string) => {
    const d = new Date(dateStr);
    const date = d.toLocaleDateString('en-CA', { weekday: 'short', month: 'short', day: 'numeric' });
    const time = d.toLocaleTimeString('en-CA', { hour: '2-digit', minute: '2-digit' });
    return { date, time };
  };

  const getTimeUntil = (dateStr: string) => {
    const diff = new Date(dateStr).getTime() - Date.now();
    if (diff <= 0) return 'Dispatching...';
    const hours = Math.floor(diff / (1000 * 60 * 60));
    const mins = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
    if (hours > 0) return `In ${hours}h ${mins}m`;
    return `In ${mins} min`;
  };

  const renderRide = ({ item }: { item: any }) => {
    const { date, time } = formatDateTime(item.scheduled_time);
    const timeUntil = getTimeUntil(item.scheduled_time);
    const isImminent = new Date(item.scheduled_time).getTime() - Date.now() < 15 * 60 * 1000;

    return (
      <View style={styles.rideCard}>
        <View style={styles.rideHeader}>
          <View style={[styles.timeBadge, isImminent && styles.timeBadgeImminent]}>
            <Ionicons name="time" size={14} color={isImminent ? '#FFF' : colors.primary} />
            <Text style={[styles.timeBadgeText, isImminent && { color: '#FFF' }]}>{timeUntil}</Text>
          </View>
          <Text style={styles.fareText}>${(item.total_fare || 0).toFixed(2)}</Text>
        </View>

        <View style={styles.scheduleRow}>
          <Ionicons name="calendar" size={16} color={colors.textDim} />
          <Text style={styles.scheduleText}>{date} at {time}</Text>
        </View>

        <View style={styles.routeSection}>
          <View style={styles.routePoint}>
            <View style={[styles.routeDot, { backgroundColor: '#10B981' }]} />
            <Text style={styles.routeAddress} numberOfLines={1}>{item.pickup_address}</Text>
          </View>
          <View style={styles.routeConnector} />
          <View style={styles.routePoint}>
            <View style={[styles.routeDot, { backgroundColor: colors.primary }]} />
            <Text style={styles.routeAddress} numberOfLines={1}>{item.dropoff_address}</Text>
          </View>
        </View>

        <View style={styles.rideFooter}>
          <View style={styles.rideStats}>
            <Text style={styles.statText}>{item.distance_km?.toFixed(1)} km</Text>
            <Text style={styles.statDot}> · </Text>
            <Text style={styles.statText}>{item.duration_minutes} min</Text>
          </View>
          <TouchableOpacity style={styles.cancelButton} onPress={() => handleCancel(item.id)}>
            <Ionicons name="close-circle-outline" size={18} color="#EF4444" />
            <Text style={styles.cancelText}>Cancel</Text>
          </TouchableOpacity>
        </View>
      </View>
    );
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <TouchableOpacity style={styles.backButton} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Scheduled Rides</Text>
        <View style={{ width: 44 }} />
      </View>

      {loading && scheduledRides.length === 0 ? (
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
      ) : scheduledRides.length === 0 ? (
        <View style={styles.emptyContainer}>
          <View style={styles.emptyIcon}>
            <Ionicons name="calendar-outline" size={48} color={colors.border} />
          </View>
          <Text style={styles.emptyText}>No scheduled rides</Text>
          <Text style={styles.emptySubtext}>
            When you book a ride for later, it will appear here
          </Text>
          <TouchableOpacity style={styles.bookButton} onPress={() => router.push('/(tabs)' as any)}>
            <Text style={styles.bookButtonText}>Book a Ride</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <FlatList
          data={scheduledRides}
          keyExtractor={(item) => item.id}
          renderItem={renderRide}
          contentContainerStyle={styles.list}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />
          }
          showsVerticalScrollIndicator={false}
          ListHeaderComponent={
            <Text style={styles.listHeader}>
              {scheduledRides.length} upcoming ride{scheduledRides.length !== 1 ? 's' : ''}
            </Text>
          }
        />
      )}

      <CustomAlert
        visible={alertState.visible}
        title={alertState.title}
        message={alertState.message}
        variant={alertState.variant}
        buttons={alertState.buttons || [{ text: 'OK', onPress: () => setAlertState(prev => ({ ...prev, visible: false })) }]}
        onClose={() => setAlertState(prev => ({ ...prev, visible: false }))}
      />
    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.background },
    header: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      paddingHorizontal: 16, paddingVertical: 12, backgroundColor: colors.surface,
      borderBottomWidth: 1, borderBottomColor: colors.border,
    },
    backButton: {
      width: 44, height: 44, borderRadius: 22, backgroundColor: colors.surfaceLight,
      alignItems: 'center', justifyContent: 'center',
    },
    headerTitle: { fontSize: 18, fontWeight: '700', color: colors.text },

    list: { padding: 16, paddingBottom: 40 },
    listHeader: { fontSize: 14, fontWeight: '600', color: colors.textDim, marginBottom: 12 },

    rideCard: {
      backgroundColor: colors.surface, borderRadius: 16, padding: 18, marginBottom: 12,
      shadowColor: '#000', shadowOpacity: 0.04, shadowOffset: { width: 0, height: 2 }, shadowRadius: 8,
      elevation: 2,
    },
    rideHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 },
    timeBadge: {
      flexDirection: 'row', alignItems: 'center', gap: 4,
      backgroundColor: colors.primary + '15', paddingHorizontal: 10, paddingVertical: 5, borderRadius: 12,
    },
    timeBadgeImminent: { backgroundColor: '#F59E0B' },
    timeBadgeText: { fontSize: 13, fontWeight: '700', color: colors.primary },
    fareText: { fontSize: 18, fontWeight: '800', color: colors.text },

    scheduleRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 14 },
    scheduleText: { fontSize: 14, color: colors.textDim },

    routeSection: { marginBottom: 14 },
    routePoint: { flexDirection: 'row', alignItems: 'center', gap: 10 },
    routeDot: { width: 10, height: 10, borderRadius: 5 },
    routeAddress: { flex: 1, fontSize: 14, color: colors.text },
    routeConnector: {
      width: 2, height: 16, backgroundColor: colors.border,
      marginLeft: 4, marginVertical: 2,
    },

    rideFooter: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
    rideStats: { flexDirection: 'row', alignItems: 'center' },
    statText: { fontSize: 13, color: colors.textDim },
    statDot: { fontSize: 13, color: colors.border },
    cancelButton: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingVertical: 4 },
    cancelText: { fontSize: 14, fontWeight: '600', color: '#EF4444' },

    loadingContainer: { flex: 1, alignItems: 'center', justifyContent: 'center' },
    emptyContainer: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 40 },
    emptyIcon: { marginBottom: 16 },
    emptyText: { fontSize: 18, fontWeight: '700', color: colors.textDim },
    emptySubtext: { fontSize: 14, color: colors.textDim, textAlign: 'center', marginTop: 8 },
    bookButton: {
      marginTop: 24, backgroundColor: colors.primary, paddingHorizontal: 32, paddingVertical: 14,
      borderRadius: 24,
    },
    bookButtonText: { fontSize: 15, fontWeight: '700', color: '#FFF' },
  });
}
