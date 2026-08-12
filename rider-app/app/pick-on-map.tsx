import React, { useState, useRef, useEffect, useMemo } from 'react';
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
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import api from '@shared/api/client';
import { useAiChatStore } from '../store/aiChatStore';

const MAP_PROVIDER = Platform.OS === 'android' ? PROVIDER_GOOGLE : undefined;

export default function PickOnMapScreen() {
  const router = useRouter();
  // ai='1': opened from the assistant's "Drop a pin" card. The confirmed pin
  // goes back into the chat (as a coordinate-carrying message) instead of the
  // manual booking flow, and aiLat/aiLng centre the map on the approximate
  // geocode the assistant is trying to pin down.
  const params = useLocalSearchParams<{ field?: string; ai?: string; aiLat?: string; aiLng?: string }>();
  const field = params.field || 'dropoff';
  const aiMode = params.ai === '1';
  const approxLat = Number(params.aiLat);
  const approxLng = Number(params.aiLng);
  const hasApprox = Number.isFinite(approxLat) && Number.isFinite(approxLng);
  const { colors, isDark } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const mapRef = useRef<MapView>(null);
  const [region, setRegion] = useState<Region | null>(null);
  const [address, setAddress] = useState('');
  const [loading, setLoading] = useState(true);
  const [geocoding, setGeocoding] = useState(false);
  const geocodeTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

  // The pin coordinates the displayed address was geocoded FOR. The reverse
  // geocode is debounced, so `address` always describes a slightly older pin
  // than `region`; on a fast pan-then-confirm the two can be blocks apart —
  // the confirmed coordinate then carries the PREVIOUS spot's address, and
  // that mismatched pair gets stored in recents and booked.
  const addressForRef = useRef<{ lat: number; lng: number } | null>(null);

  const reverseGeocode = async (lat: number, lng: number) => {
    setGeocoding(true);
    try {
      const params = new URLSearchParams({ lat: String(lat), lng: String(lng) });
      const { data } = await api.get<{ formatted_address: string }>(
        `/maps/reverse-geocode?${params.toString()}`,
      );
      if (data?.formatted_address) {
        setAddress(data.formatted_address);
      } else {
        setAddress(`${lat.toFixed(5)}, ${lng.toFixed(5)}`);
      }
    } catch {
      setAddress(`${lat.toFixed(5)}, ${lng.toFixed(5)}`);
    } finally {
      addressForRef.current = { lat, lng };
      setGeocoding(false);
    }
  };

  useEffect(() => {
    (async () => {
      // The assistant supplied an approximate point — start there instead of
      // the device GPS (the whole reason we're here is to refine THAT spot),
      // and geocode it immediately so the confirm button isn't stuck waiting
      // for a map interaction.
      if (hasApprox) {
        setRegion({ latitude: approxLat, longitude: approxLng, latitudeDelta: 0.008, longitudeDelta: 0.008 });
        setLoading(false);
        reverseGeocode(approxLat, approxLng);
        return;
      }
      let { status } = await Location.getForegroundPermissionsAsync();
      if (status !== 'granted') {
        const res = await Location.requestForegroundPermissionsAsync();
        status = res.status;
      }
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

  /** ~metres between two points at city scale (equirectangular — fine under a
   * few km, and we only compare against a 60 m threshold). */
  const approxMeters = (a: { lat: number; lng: number }, b: { lat: number; lng: number }) => {
    const dLat = (a.lat - b.lat) * 111_320;
    const dLng = (a.lng - b.lng) * 111_320 * Math.cos((a.lat * Math.PI) / 180);
    return Math.sqrt(dLat * dLat + dLng * dLng);
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
    // Only pass the address if it was geocoded for (approximately) THIS pin.
    // A stale label from the pre-pan position must not travel with the new
    // coordinate — the coordinate is what gets booked, so when the two
    // disagree the honest label is the coordinate itself.
    const pin = { lat: region.latitude, lng: region.longitude };
    const addressMatchesPin =
      !!address && !geocoding && !!addressForRef.current && approxMeters(pin, addressForRef.current) <= 60;
    if (aiMode) {
      // Return leg of the assistant's "Drop a pin" card: the pin goes back
      // into the chat as a coordinate-carrying message. A stale label still
      // must not travel — without a matching address the message carries the
      // coordinates alone, which is exactly what the model needs.
      void useAiChatStore
        .getState()
        .submitMapPin(field === 'pickup' ? 'pickup' : 'dropoff', {
          lat: pin.lat,
          lng: pin.lng,
          address: addressMatchesPin ? address : null,
        });
      router.back();
      return;
    }
    router.navigate({
      pathname: '/search-destination',
      params: {
        mapPickField: field,
        mapPickLat: String(region.latitude),
        mapPickLng: String(region.longitude),
        mapPickAddress: addressMatchesPin ? address : `${region.latitude.toFixed(5)}, ${region.longitude.toFixed(5)}`,
      },
    } as any);
  };

  const handleRecenter = async () => {
    try {
      // The AI-mode mount skips the permission request (the map opens on the
      // assistant's approximate point, not the device), so permission may
      // never have been asked — request it here or the button is silently
      // dead in exactly that flow.
      let { status } = await Location.getForegroundPermissionsAsync();
      if (status !== 'granted') {
        status = (await Location.requestForegroundPermissionsAsync()).status;
      }
      if (status !== 'granted') return;
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
        <ActivityIndicator size="large" color={colors.primary} />
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
        userInterfaceStyle={isDark ? "dark" : "light"}
      />

      {/* Center pin (fixed in center of screen) */}
      <View style={styles.pinContainer} pointerEvents="none">
        <View style={styles.pinShadow} />
        <Ionicons name="location" size={40} color={colors.primary} style={styles.pin} />
      </View>

      {/* Header */}
      <SafeAreaView style={styles.headerOverlay} edges={['top']}>
        <View style={styles.header}>
          <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
            <Ionicons name="arrow-back" size={24} color={colors.text} />
          </TouchableOpacity>
          <Text style={styles.headerTitle}>
            {field === 'pickup' ? 'Set pickup location' : 'Set destination'}
          </Text>
          <View style={{ width: 44 }} />
        </View>
      </SafeAreaView>

      {/* Recenter button */}
      <TouchableOpacity style={styles.recenterBtn} onPress={handleRecenter}>
        <Ionicons name="locate" size={22} color={colors.text} />
      </TouchableOpacity>

      {/* Bottom card */}
      <View style={styles.bottomCard}>
        <View style={styles.addressRow}>
          <View style={[styles.addressDot, {
            backgroundColor: field === 'pickup' ? '#10B981' : colors.primary,
          }]} />
          <View style={styles.addressContent}>
            {geocoding ? (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <ActivityIndicator size="small" color={colors.textDim} />
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

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.surface },
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
      backgroundColor: colors.surface,
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
      color: colors.text,
      backgroundColor: colors.surface,
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
      backgroundColor: colors.surface,
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
      backgroundColor: colors.surface,
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
      color: colors.text,
      lineHeight: 20,
    },
    addressLoading: {
      fontSize: 14,
      color: colors.textDim,
    },
    confirmBtn: {
      backgroundColor: colors.primary,
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
}
