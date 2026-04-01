import React, { useState, useRef, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ActivityIndicator,
  Platform,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import MapView, { PROVIDER_GOOGLE, Region } from 'react-native-maps';
import * as Location from 'expo-location';
import SpinrConfig from '@shared/config/spinr.config';

const GOOGLE_MAPS_API_KEY = process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY;
const MAP_PROVIDER = Platform.OS === 'android' ? PROVIDER_GOOGLE : undefined;

export default function PickOnMapScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ field?: string }>();
  const field = params.field || 'dropoff';

  const mapRef = useRef<MapView>(null);
  const [region, setRegion] = useState<Region | null>(null);
  const [address, setAddress] = useState('');
  const [loading, setLoading] = useState(true);
  const [geocoding, setGeocoding] = useState(false);
  const geocodeTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    (async () => {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        setRegion({ latitude: 52.1332, longitude: -106.67, latitudeDelta: 0.02, longitudeDelta: 0.02 });
        setLoading(false);
        return;
      }
      try {
        const loc = await Location.getCurrentPositionAsync({});
        setRegion({
          latitude: loc.coords.latitude,
          longitude: loc.coords.longitude,
          latitudeDelta: 0.008,
          longitudeDelta: 0.008,
        });
      } catch {
        setRegion({ latitude: 52.1332, longitude: -106.67, latitudeDelta: 0.02, longitudeDelta: 0.02 });
      }
      setLoading(false);
    })();
  }, []);

  const reverseGeocode = async (lat: number, lng: number) => {
    if (!GOOGLE_MAPS_API_KEY) {
      setAddress(`${lat.toFixed(5)}, ${lng.toFixed(5)}`);
      return;
    }
    setGeocoding(true);
    try {
      const res = await fetch(
        `https://maps.googleapis.com/maps/api/geocode/json?latlng=${lat},${lng}&key=${GOOGLE_MAPS_API_KEY}`
      );
      const data = await res.json();
      if (data.results?.[0]) {
        setAddress(data.results[0].formatted_address);
      } else {
        setAddress(`${lat.toFixed(5)}, ${lng.toFixed(5)}`);
      }
    } catch {
      setAddress(`${lat.toFixed(5)}, ${lng.toFixed(5)}`);
    } finally {
      setGeocoding(false);
    }
  };

  const handleRegionChange = (newRegion: Region) => {
    setRegion(newRegion);
    // Debounce reverse geocoding
    if (geocodeTimeout.current) clearTimeout(geocodeTimeout.current);
    geocodeTimeout.current = setTimeout(() => {
      reverseGeocode(newRegion.latitude, newRegion.longitude);
    }, 500);
  };

  const handleConfirm = () => {
    if (!region) return;
    // Pass back the selected location via params
    router.navigate({
      pathname: '/search-destination',
      params: {
        mapPickField: field,
        mapPickLat: String(region.latitude),
        mapPickLng: String(region.longitude),
        mapPickAddress: address || `${region.latitude.toFixed(5)}, ${region.longitude.toFixed(5)}`,
      },
    } as any);
  };

  const handleRecenter = async () => {
    try {
      const loc = await Location.getCurrentPositionAsync({});
      mapRef.current?.animateToRegion({
        latitude: loc.coords.latitude,
        longitude: loc.coords.longitude,
        latitudeDelta: 0.008,
        longitudeDelta: 0.008,
      }, 500);
    } catch {}
  };

  if (loading || !region) {
    return (
      <View style={[styles.container, { justifyContent: 'center', alignItems: 'center' }]}>
        <ActivityIndicator size="large" color={SpinrConfig.theme.colors.primary} />
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {/* Map */}
      <MapView
        ref={mapRef}
        style={styles.map}
        provider={MAP_PROVIDER}
        initialRegion={region}
        onRegionChangeComplete={handleRegionChange}
        showsUserLocation
        showsMyLocationButton={false}
      />

      {/* Center pin (fixed in center of screen) */}
      <View style={styles.pinContainer} pointerEvents="none">
        <View style={styles.pinShadow} />
        <Ionicons name="location" size={40} color={SpinrConfig.theme.colors.primary} style={styles.pin} />
      </View>

      {/* Header */}
      <SafeAreaView style={styles.headerOverlay} edges={['top']}>
        <View style={styles.header}>
          <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
            <Ionicons name="arrow-back" size={24} color="#1A1A1A" />
          </TouchableOpacity>
          <Text style={styles.headerTitle}>
            {field === 'pickup' ? 'Set pickup location' : 'Set destination'}
          </Text>
          <View style={{ width: 44 }} />
        </View>
      </SafeAreaView>

      {/* Recenter button */}
      <TouchableOpacity style={styles.recenterBtn} onPress={handleRecenter}>
        <Ionicons name="locate" size={22} color="#1A1A1A" />
      </TouchableOpacity>

      {/* Bottom card */}
      <View style={styles.bottomCard}>
        <View style={styles.addressRow}>
          <View style={[styles.addressDot, {
            backgroundColor: field === 'pickup' ? '#10B981' : SpinrConfig.theme.colors.primary,
          }]} />
          <View style={styles.addressContent}>
            {geocoding ? (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <ActivityIndicator size="small" color="#999" />
                <Text style={styles.addressLoading}>Finding address...</Text>
              </View>
            ) : (
              <Text style={styles.addressText} numberOfLines={2}>
                {address || 'Move the map to select a location'}
              </Text>
            )}
          </View>
        </View>
        <TouchableOpacity
          style={[styles.confirmBtn, (!address || geocoding) && styles.confirmBtnDisabled]}
          onPress={handleConfirm}
          disabled={!address || geocoding}
        >
          <Text style={styles.confirmBtnText}>Confirm Location</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#FFF' },
  map: { flex: 1 },

  pinContainer: {
    position: 'absolute',
    top: '50%',
    left: '50%',
    marginLeft: -20,
    marginTop: -44,
    alignItems: 'center',
    zIndex: 10,
  },
  pin: {
    textShadowColor: 'rgba(0,0,0,0.2)',
    textShadowOffset: { width: 0, height: 2 },
    textShadowRadius: 4,
  },
  pinShadow: {
    position: 'absolute',
    bottom: -2,
    width: 10,
    height: 4,
    borderRadius: 5,
    backgroundColor: 'rgba(0,0,0,0.2)',
  },

  headerOverlay: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    zIndex: 20,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 8,
  },
  backBtn: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: '#FFF',
    justifyContent: 'center',
    alignItems: 'center',
    elevation: 4,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.15,
    shadowRadius: 4,
  },
  headerTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: '#1A1A1A',
    backgroundColor: '#FFF',
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 20,
    elevation: 4,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.15,
    shadowRadius: 4,
    overflow: 'hidden',
  },

  recenterBtn: {
    position: 'absolute',
    right: 16,
    bottom: 190,
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: '#FFF',
    justifyContent: 'center',
    alignItems: 'center',
    elevation: 4,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.15,
    shadowRadius: 4,
  },

  bottomCard: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    backgroundColor: '#FFF',
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    paddingHorizontal: 20,
    paddingTop: 20,
    paddingBottom: Platform.OS === 'ios' ? 36 : 24,
    elevation: 12,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: -4 },
    shadowOpacity: 0.1,
    shadowRadius: 8,
  },
  addressRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 16,
  },
  addressDot: {
    width: 12,
    height: 12,
    borderRadius: 6,
    marginRight: 12,
  },
  addressContent: { flex: 1 },
  addressText: {
    fontSize: 15,
    fontWeight: '500',
    color: '#1A1A1A',
    lineHeight: 20,
  },
  addressLoading: {
    fontSize: 14,
    color: '#999',
  },
  confirmBtn: {
    backgroundColor: SpinrConfig.theme.colors.primary,
    borderRadius: 28,
    paddingVertical: 16,
    alignItems: 'center',
  },
  confirmBtnDisabled: {
    backgroundColor: '#CCC',
  },
  confirmBtnText: {
    color: '#FFF',
    fontSize: 17,
    fontWeight: '700',
  },
});
