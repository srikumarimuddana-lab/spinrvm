import React, { useState, useEffect, useRef } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Alert, Dimensions, Switch, ActivityIndicator, Image, Platform } from 'react-native';
import * as Location from 'expo-location';
import { useAuthStore } from '../../store/authStore';
import SpinrConfig from '../../config/spinr.config';
import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';

import AppMap from '../../components/AppMap';

export default function DriverDashboard() {
  const router = useRouter();
  const { user, driver, token, updateDriverStatus } = useAuthStore();
  const [location, setLocation] = useState<Location.LocationObject | null>(null);
  const [isOnline, setIsOnline] = useState(false);
  const [incomingRide, setIncomingRide] = useState<any>(null);
  const [currentRide, setCurrentRide] = useState<any>(null);
  const mapRef = useRef<any>(null);
  const wsRef = useRef<WebSocket | null>(null);

  // Initialize location
  useEffect(() => {
    (async () => {
      let { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        Alert.alert('Permission denied', 'Allow location access to receive rides');
        return;
      }

      let loc = await Location.getCurrentPositionAsync({});
      setLocation(loc);
    })();
  }, []);

  // Sync online status
  useEffect(() => {
    if (driver) {
      setIsOnline(driver.is_online);
    }
  }, [driver]);

  // WebSocket connection
  useEffect(() => {
    if (!token || !user) return;

    const wsUrl = SpinrConfig.backendUrl.replace('http', 'ws') + `/ws/driver/${user.id}`;
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      console.log('Driver WS Connected');
      ws.send(JSON.stringify({ type: 'auth', token }));
    };

    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        console.log('Driver WS Message:', data);

        if (data.type === 'new_ride_assignment') {
          setIncomingRide(data);
          // Auto-focus map if needed?
        }
      } catch (err) {
        console.log('WS Parse Error', err);
      }
    };

    ws.onclose = () => {
      console.log('Driver WS Disconnected');
    };

    wsRef.current = ws;

    return () => {
      ws.close();
    };
  }, [token, user]);

  // Location tracking loop
  useEffect(() => {
    if (!isOnline || !wsRef.current || !driver) return;

    const interval = setInterval(async () => {
      try {
        const loc = await Location.getCurrentPositionAsync({});
        setLocation(loc);

        if (wsRef.current?.readyState === WebSocket.OPEN) {
          wsRef.current.send(JSON.stringify({
            type: 'driver_location',
            driver_id: driver.id,
            lat: loc.coords.latitude,
            lng: loc.coords.longitude
          }));
        }
      } catch (e) {
        console.log('Loc update error', e);
      }
    }, 10000);

    return () => clearInterval(interval);
  }, [isOnline, driver]);

  const handleToggleOnline = async (value: boolean) => {
    setIsOnline(value); // Optimistic update
    await updateDriverStatus(value);
  };

  const handleAcceptRide = () => {
    setCurrentRide(incomingRide);
    setIncomingRide(null);
  };

  // Simulate ride actions (since backend has specific endpoints for arrival/start/complete)
  // For now, we just mock the UI flow or call endpoints if we had them hooked up in store.
  // We'll just show current ride state.

  return (
    <View style={styles.container}>
      {location ? (
        <AppMap
          ref={mapRef}
          style={styles.map}
          initialRegion={{
            latitude: location.coords.latitude,
            longitude: location.coords.longitude,
            latitudeDelta: 0.01,
            longitudeDelta: 0.01,
          }}
          showsUserLocation
        >
          {/* Show pickup/dropoff markers if ride active */}
        </AppMap>
      ) : location ? (
        <View style={[styles.loadingContainer, { backgroundColor: '#e8f4f8' }]}>
          <Ionicons name="map-outline" size={64} color="#999" />
          <Text style={{ marginTop: 12, fontSize: 16, color: '#666' }}>Map view is only available on mobile</Text>
          <Text style={{ marginTop: 4, fontSize: 13, color: '#999' }}>
            Lat: {location.coords.latitude.toFixed(4)}, Lng: {location.coords.longitude.toFixed(4)}
          </Text>
        </View>
      ) : (
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color={SpinrConfig.theme.colors.primary} />
          <Text>Locating...</Text>
        </View>
      )}

      {/* Top Bar: Online Status */}
      <View style={styles.topBar}>
        <Text style={styles.statusText}>{isOnline ? 'You are Online' : 'You are Offline'}</Text>
        <Switch
          trackColor={{ false: "#767577", true: "#81b0ff" }}
          thumbColor={isOnline ? SpinrConfig.theme.colors.primary : "#f4f3f4"}
          onValueChange={handleToggleOnline}
          value={isOnline}
        />
      </View>

      {/* Incoming Ride Modal / Overlay */}
      {incomingRide && (
        <View style={styles.requestContainer}>
          <Text style={styles.requestTitle}>New Ride Request!</Text>
          <View style={styles.rideDetails}>
            <View style={styles.detailRow}>
              <Ionicons name="location" size={20} color="green" />
              <Text style={styles.detailText}>{incomingRide.pickup_address}</Text>
            </View>
            <View style={styles.detailRow}>
              <Ionicons name="flag" size={20} color="red" />
              <Text style={styles.detailText}>{incomingRide.dropoff_address}</Text>
            </View>
            <Text style={styles.fareText}>Est. Earnings: ${incomingRide.fare}</Text>
          </View>
          <TouchableOpacity style={styles.acceptButton} onPress={handleAcceptRide}>
            <Text style={styles.acceptButtonText}>Accept Ride</Text>
          </TouchableOpacity>
        </View>
      )}

      {/* Current Ride Overlay */}
      {currentRide && (
        <View style={styles.rideContainer}>
          <Text style={styles.rideTitle}>Current Ride</Text>
          <Text>Picking up at: {currentRide.pickup_address}</Text>
          <TouchableOpacity
            style={styles.actionButton}
            onPress={() => {
              // Logic to arrive/start/complete would go here.
              // For now, let's just clear it to "complete" simulation locally
              Alert.alert('Ride Completed', 'You earned $' + currentRide.fare);
              setCurrentRide(null);
            }}
          >
            <Text style={styles.actionButtonText}>Complete Ride (Simulated)</Text>
          </TouchableOpacity>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#fff',
  },
  map: {
    width: Dimensions.get('window').width,
    height: Dimensions.get('window').height,
  },
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  topBar: {
    position: 'absolute',
    top: 50,
    left: 20,
    right: 20,
    backgroundColor: 'white',
    borderRadius: 16,
    padding: 16,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
    elevation: 5,
  },
  statusText: {
    fontSize: 18,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
  },
  requestContainer: {
    position: 'absolute',
    bottom: 40,
    left: 20,
    right: 20,
    backgroundColor: 'white',
    borderRadius: 20,
    padding: 24,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.2,
    shadowRadius: 8,
    elevation: 10,
    borderWidth: 2,
    borderColor: SpinrConfig.theme.colors.primary,
  },
  requestTitle: {
    fontSize: 22,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
    marginBottom: 16,
    textAlign: 'center',
  },
  rideDetails: {
    marginBottom: 20,
  },
  detailRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 12,
  },
  detailText: {
    marginLeft: 10,
    fontSize: 16,
    color: '#333',
    flex: 1,
  },
  fareText: {
    fontSize: 20,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: 'green',
    textAlign: 'center',
    marginTop: 10,
  },
  acceptButton: {
    backgroundColor: SpinrConfig.theme.colors.primary,
    borderRadius: 16,
    paddingVertical: 16,
    alignItems: 'center',
  },
  acceptButtonText: {
    color: 'white',
    fontSize: 18,
    fontFamily: 'PlusJakartaSans_700Bold',
  },
  rideContainer: {
    position: 'absolute',
    bottom: 40,
    left: 20,
    right: 20,
    backgroundColor: 'white',
    borderRadius: 20,
    padding: 24,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.2,
    shadowRadius: 8,
    elevation: 10,
  },
  rideTitle: {
    fontSize: 20,
    fontFamily: 'PlusJakartaSans_700Bold',
    marginBottom: 10,
  },
  actionButton: {
    backgroundColor: '#333',
    borderRadius: 12,
    padding: 14,
    marginTop: 20,
    alignItems: 'center',
  },
  actionButtonText: {
    color: 'white',
    fontWeight: 'bold',
  },
});
