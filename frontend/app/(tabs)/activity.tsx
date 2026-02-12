import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  RefreshControl,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import { useRideStore } from '../../store/rideStore';
import SpinrConfig from '../../config/spinr.config';
import api from '../../api/client';
import { useAuthStore } from '../../store/authStore';

interface RideHistory {
  id: string;
  pickup_address: string;
  dropoff_address: string;
  total_fare: number;
  distance_km: number;
  duration_minutes: number;
  status: string;
  vehicle_type_id: string;
  created_at: string;
}

type FilterType = 'all' | 'personal' | 'business';

export default function ActivityScreen() {
  const router = useRouter();
  const { token } = useAuthStore();
  const [rides, setRides] = useState<RideHistory[]>([]);
  const [filter, setFilter] = useState<FilterType>('all');
  const [refreshing, setRefreshing] = useState(false);
  const [loading, setLoading] = useState(true);

  const fetchRides = async () => {
    try {
      const response = await api.get('/rides');
      setRides(response.data || []);
    } catch (error) {
      console.log('Error fetching rides:', error);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchRides();
  }, []);

  const onRefresh = () => {
    setRefreshing(true);
    fetchRides();
  };

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    const now = new Date();
    const isToday = date.toDateString() === now.toDateString();
    const isYesterday = new Date(now.getTime() - 86400000).toDateString() === date.toDateString();

    if (isToday) {
      return `Today, ${date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`;
    } else if (isYesterday) {
      return `Yesterday, ${date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`;
    } else {
      return date.toLocaleDateString([], { month: 'short', day: 'numeric' }) + 
        `, ${date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`;
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'completed':
        return '#10B981';
      case 'cancelled':
        return '#999';
      case 'in_progress':
        return '#3B82F6';
      default:
        return '#FFB800';
    }
  };

  const getStatusText = (status: string) => {
    switch (status) {
      case 'completed':
        return 'Completed';
      case 'cancelled':
        return 'CANCELLED';
      case 'in_progress':
        return 'In Progress';
      case 'driver_assigned':
        return 'Driver On Way';
      case 'driver_arrived':
        return 'Driver Arrived';
      default:
        return status;
    }
  };

  const getRideIcon = (status: string) => {
    if (status === 'cancelled') {
      return 'arrow-down-outline';
    }
    return 'car';
  };

  const getRideIconBg = (status: string) => {
    if (status === 'cancelled') {
      return '#FFF0F0';
    }
    return '#FFF0F0';
  };

  const getVehicleType = (id: string) => {
    // Mock vehicle type mapping
    if (id.includes('xl') || id.includes('XL')) return 'XL';
    if (id.includes('lux') || id.includes('Lux')) return 'Comfort';
    return 'Standard';
  };

  // Group rides by month
  const groupRidesByMonth = (rides: RideHistory[]) => {
    const groups: { [key: string]: RideHistory[] } = {};
    const now = new Date();
    
    rides.forEach(ride => {
      const date = new Date(ride.created_at);
      const isRecent = (now.getTime() - date.getTime()) < 7 * 24 * 60 * 60 * 1000; // 7 days
      
      const key = isRecent ? 'RECENT' : date.toLocaleDateString([], { month: 'long' }).toUpperCase();
      
      if (!groups[key]) {
        groups[key] = [];
      }
      groups[key].push(ride);
    });
    
    return groups;
  };

  const groupedRides = groupRidesByMonth(rides);

  const handleRidePress = (ride: RideHistory) => {
    // Navigate to ride details
    if (ride.status === 'in_progress' || ride.status === 'driver_assigned') {
      router.push({ pathname: '/ride-in-progress', params: { rideId: ride.id } });
    }
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.title}>Activity</Text>
        <TouchableOpacity style={styles.filterIcon}>
          <Ionicons name="options-outline" size={24} color="#1A1A1A" />
        </TouchableOpacity>
      </View>

      {/* Filter Tabs */}
      <View style={styles.filterTabs}>
        <TouchableOpacity
          style={[styles.filterTab, filter === 'all' && styles.filterTabActive]}
          onPress={() => setFilter('all')}
        >
          <Text style={[styles.filterTabText, filter === 'all' && styles.filterTabTextActive]}>All</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.filterTab, filter === 'personal' && styles.filterTabActive]}
          onPress={() => setFilter('personal')}
        >
          <Text style={[styles.filterTabText, filter === 'personal' && styles.filterTabTextActive]}>Personal</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.filterTab, filter === 'business' && styles.filterTabActive]}
          onPress={() => setFilter('business')}
        >
          <Text style={[styles.filterTabText, filter === 'business' && styles.filterTabTextActive]}>Business</Text>
        </TouchableOpacity>
      </View>

      {/* Rides List */}
      <ScrollView
        style={styles.content}
        contentContainerStyle={styles.contentContainer}
        showsVerticalScrollIndicator={false}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} />
        }
      >
        {rides.length === 0 && !loading ? (
          <View style={styles.emptyState}>
            <View style={styles.emptyIconContainer}>
              <Ionicons name="car-outline" size={48} color="#CCC" />
            </View>
            <Text style={styles.emptyTitle}>No rides yet</Text>
            <Text style={styles.emptyText}>
              Your ride history will appear here once{"\n"}you complete your first trip.
            </Text>
          </View>
        ) : (
          Object.entries(groupedRides).map(([month, monthRides]) => (
            <View key={month}>
              <Text style={styles.monthHeader}>{month}</Text>
              {monthRides.map((ride) => (
                <TouchableOpacity
                  key={ride.id}
                  style={styles.rideCard}
                  onPress={() => handleRidePress(ride)}
                >
                  <View style={[styles.rideIcon, { backgroundColor: getRideIconBg(ride.status) }]}>
                    <Ionicons
                      name={getRideIcon(ride.status) as any}
                      size={20}
                      color={ride.status === 'cancelled' ? SpinrConfig.theme.colors.primary : SpinrConfig.theme.colors.primary}
                    />
                  </View>
                  
                  <View style={styles.rideDetails}>
                    <Text style={styles.rideDestination} numberOfLines={1}>
                      {ride.dropoff_address || 'Unknown destination'}
                    </Text>
                    <Text style={styles.rideInfo}>
                      {formatDate(ride.created_at)} • {getVehicleType(ride.vehicle_type_id)}
                    </Text>
                  </View>
                  
                  <View style={styles.rideFareContainer}>
                    <Text style={[styles.rideFare, ride.status === 'cancelled' && styles.rideFareCancelled]}>
                      ${ride.status === 'cancelled' ? '0.00' : ride.total_fare?.toFixed(2) || '0.00'}
                    </Text>
                    <View style={styles.rideStatusContainer}>
                      <View style={[styles.statusDot, { backgroundColor: getStatusColor(ride.status) }]} />
                      <Text style={[styles.rideStatus, { color: getStatusColor(ride.status) }]}>
                        {getStatusText(ride.status)}
                      </Text>
                    </View>
                  </View>
                </TouchableOpacity>
              ))}
            </View>
          ))
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#FFFFFF',
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 24,
    paddingVertical: 16,
  },
  title: {
    fontSize: 28,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
  },
  filterIcon: {
    padding: 4,
  },
  filterTabs: {
    flexDirection: 'row',
    paddingHorizontal: 20,
    marginBottom: 8,
    gap: 8,
  },
  filterTab: {
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: 24,
    borderWidth: 1.5,
    borderColor: '#E0E0E0',
    backgroundColor: '#FFF',
  },
  filterTabActive: {
    backgroundColor: '#1A1A1A',
    borderColor: '#1A1A1A',
  },
  filterTabText: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
  },
  filterTabTextActive: {
    color: '#FFF',
  },
  content: {
    flex: 1,
  },
  contentContainer: {
    padding: 20,
    paddingTop: 12,
  },
  monthHeader: {
    fontSize: 12,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#999',
    letterSpacing: 0.5,
    marginTop: 16,
    marginBottom: 12,
  },
  rideCard: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 16,
    borderBottomWidth: 1,
    borderBottomColor: '#F5F5F5',
  },
  rideIcon: {
    width: 48,
    height: 48,
    borderRadius: 24,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 14,
  },
  rideDetails: {
    flex: 1,
  },
  rideDestination: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
    marginBottom: 4,
  },
  rideInfo: {
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#999',
  },
  rideFareContainer: {
    alignItems: 'flex-end',
  },
  rideFare: {
    fontSize: 17,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: SpinrConfig.theme.colors.primary,
    marginBottom: 4,
  },
  rideFareCancelled: {
    color: '#999',
  },
  rideStatusContainer: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  statusDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    marginRight: 4,
  },
  rideStatus: {
    fontSize: 12,
    fontFamily: 'PlusJakartaSans_500Medium',
  },
  emptyState: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingTop: 100,
  },
  emptyIconContainer: {
    width: 100,
    height: 100,
    borderRadius: 50,
    backgroundColor: '#F5F5F5',
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 24,
  },
  emptyTitle: {
    fontSize: 20,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
    marginBottom: 8,
  },
  emptyText: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#666',
    textAlign: 'center',
    lineHeight: 22,
  },
});
