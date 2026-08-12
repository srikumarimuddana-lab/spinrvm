import React, { useState, useRef, useMemo, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ActivityIndicator,
  Platform,
  TextInput,
  Modal,
  ScrollView,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import MapView, { Circle, PROVIDER_GOOGLE, Region } from 'react-native-maps';
import { useRideStore } from '../store/rideStore';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useResponsive } from '@shared/utils/responsive';
import api from '@shared/api/client';

const MAP_PROVIDER = Platform.OS === 'android' ? PROVIDER_GOOGLE : undefined;
const PICKUP_RADIUS_M = 50;

function haversineM(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const R = 6371000;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLng = toRad(lng2 - lng1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

export default function ConfirmPickupScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors, isDark } = useTheme();
  const { sf } = useResponsive();
  const styles = useMemo(() => createStyles(colors, sf, insets), [colors, sf, insets]);

  const { pickup, setPickup, dropoff, riderNotes, setRiderNotes } = useRideStore();
  const [note, setNote] = useState(riderNotes ?? '');
  const mapRef = useRef<MapView>(null);
  const geocodeTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

  const originalLat = useRef(pickup?.lat ?? 52.1332);
  const originalLng = useRef(pickup?.lng ?? -106.67);

  const [region, setRegion] = useState<Region>({
    latitude: originalLat.current,
    longitude: originalLng.current,
    latitudeDelta: 0.003,
    longitudeDelta: 0.003,
  });
  const [address, setAddress] = useState(pickup?.address ?? '');
  const [geocoding, setGeocoding] = useState(false);
  const [distanceM, setDistanceM] = useState(0);
  const [checking, setChecking] = useState(false);
  const [chooser, setChooser] = useState<{ venue: string; points: { name: string; lat: number; lng: number }[] } | null>(null);
  // Measured bottom-card height so the recenter button floats just above it
  // instead of overlapping — the card grows with the too-far warning, the
  // dropoff row, and the multiline note. Falls back to a default until first layout.
  const [cardHeight, setCardHeight] = useState(0);

  const isTooFar = distanceM > PICKUP_RADIUS_M;

  // Coordinates the displayed address was geocoded FOR. Starts at the original
  // pin, whose address came in with it from the store. The reverse geocode is
  // debounced 400 ms, so on a fast pan-then-confirm `address` still describes
  // the previous pin — booking would bind the old label to the new coordinate.
  const addressForRef = useRef<{ lat: number; lng: number }>({
    lat: originalLat.current,
    lng: originalLng.current,
  });

  const reverseGeocode = useCallback(async (lat: number, lng: number) => {
    setGeocoding(true);
    try {
      const qs = new URLSearchParams({ lat: String(lat), lng: String(lng) });
      const { data } = await api.get<{ formatted_address: string }>(
        `/maps/reverse-geocode?${qs.toString()}`,
      );
      setAddress(data?.formatted_address || `${lat.toFixed(5)}, ${lng.toFixed(5)}`);
    } catch {
      setAddress(`${lat.toFixed(5)}, ${lng.toFixed(5)}`);
    } finally {
      addressForRef.current = { lat, lng };
      setGeocoding(false);
    }
  }, []);

  const handleRegionChange = useCallback((r: Region) => {
    setRegion(r);
    const d = haversineM(originalLat.current, originalLng.current, r.latitude, r.longitude);
    setDistanceM(d);
    if (geocodeTimeout.current) clearTimeout(geocodeTimeout.current);
    geocodeTimeout.current = setTimeout(() => reverseGeocode(r.latitude, r.longitude), 400);
  }, [reverseGeocode]);

  const proceed = (lat: number, lng: number, addr: string, noteValue: string) => {
    setPickup({ address: addr, lat, lng });
    setRiderNotes(noteValue.trim());
    router.push('/ride-options');
  };

  const handleConfirm = async () => {
    // If the pin is inside a known venue (mall/airport), let the rider pick a
    // curated, driver-reachable meeting point instead of an unreachable pin.
    setChecking(true);
    try {
      const { data } = await api.get<{ venue: { name: string } | null; pickup_points: { name: string; lat: number; lng: number }[] }>(
        `/maps/pickup-points?lat=${region.latitude}&lng=${region.longitude}`,
      );
      if (data?.venue && Array.isArray(data.pickup_points) && data.pickup_points.length > 0) {
        setChooser({ venue: data.venue.name, points: data.pickup_points });
        setChecking(false);
        return;
      }
    } catch {
      // No venue match / lookup failed → fall through to the plain pin.
    }
    setChecking(false);
    // Never bind a label to a pin it wasn't geocoded for — when they disagree
    // (mid-debounce confirm), the honest label for the booked coordinate is
    // the coordinate itself.
    const labelCurrent =
      !!address &&
      !geocoding &&
      haversineM(region.latitude, region.longitude, addressForRef.current.lat, addressForRef.current.lng) <= 60;
    proceed(
      region.latitude,
      region.longitude,
      labelCurrent ? address : `${region.latitude.toFixed(5)}, ${region.longitude.toFixed(5)}`,
      note,
    );
  };

  const handleRecenter = () => {
    mapRef.current?.animateToRegion(
      {
        latitude: originalLat.current,
        longitude: originalLng.current,
        latitudeDelta: 0.003,
        longitudeDelta: 0.003,
      },
      400,
    );
  };

  if (!pickup) {
    router.back();
    return null;
  }

  return (
    <View style={styles.container}>
      {/* ═══ Full-screen map ═══ */}
      <MapView
        ref={mapRef}
        style={styles.map}
        provider={MAP_PROVIDER}
        initialRegion={region}
        onRegionChangeComplete={handleRegionChange}
        showsUserLocation
        showsMyLocationButton={false}
        userInterfaceStyle={isDark ? 'dark' : 'light'}
      >
        {/* 50 m suggested radius */}
        <Circle
          center={{ latitude: originalLat.current, longitude: originalLng.current }}
          radius={PICKUP_RADIUS_M}
          fillColor="rgba(16,185,129,0.10)"
          strokeColor="rgba(16,185,129,0.35)"
          strokeWidth={1.5}
        />
      </MapView>

      {/* ═══ Fixed center pin ═══ */}
      <View style={styles.pinContainer} pointerEvents="none">
        <Ionicons name="location" size={44} color={colors.primary} style={styles.pinIcon} />
        <View style={styles.pinShadow} />
      </View>

      {/* ═══ Floating back button ═══ */}
      <TouchableOpacity
        style={[styles.floatingBack, { top: insets.top + 8 }]}
        onPress={() => router.back()}
        accessibilityRole="button"
        accessibilityLabel="Go back"
      >
        <Ionicons name="arrow-back" size={22} color={colors.text} />
      </TouchableOpacity>

      {/* ═══ Title pill ═══ */}
      <View style={[styles.titlePill, { top: insets.top + 14 }]}>
        <Text style={styles.titlePillText}>Confirm pickup</Text>
      </View>

      {/* ═══ Recenter button ═══ */}
      <TouchableOpacity
        style={[styles.recenterBtn, { bottom: (cardHeight || 240) + 12 }]}
        onPress={handleRecenter}
        accessibilityRole="button"
        accessibilityLabel="Recenter map on your location"
      >
        <Ionicons name="locate" size={22} color={colors.text} />
      </TouchableOpacity>

      {/* ═══ Bottom card ═══ */}
      <View
        style={[styles.bottomCard, { paddingBottom: Math.max(insets.bottom, 16) + 8 }]}
        onLayout={(e) => setCardHeight(e.nativeEvent.layout.height)}
      >
        {/* Instruction */}
        <Text style={styles.instruction}>
          Move the map to set your exact pickup spot
        </Text>

        {/* Address row */}
        <View style={styles.addressRow}>
          <View style={styles.addressDot} />
          <View style={{ flex: 1 }}>
            {geocoding ? (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <ActivityIndicator size="small" color={colors.textDim} />
                <Text style={styles.addressLoading}>Finding address...</Text>
              </View>
            ) : (
              <Text style={styles.addressText} numberOfLines={2}>{address}</Text>
            )}
          </View>
        </View>

        {/* Too-far warning */}
        {isTooFar && (
          <View style={styles.warningRow}>
            <Ionicons name="information-circle" size={16} color="#D97706" />
            <Text style={styles.warningText}>
              You&apos;ve moved {Math.round(distanceM)}m from your searched location
            </Text>
          </View>
        )}

        {/* Dropoff reminder */}
        {dropoff && (
          <View style={styles.dropoffRow}>
            <View style={[styles.addressDot, { backgroundColor: colors.primary }]} />
            <Text style={styles.dropoffText} numberOfLines={1}>To: {dropoff.address}</Text>
          </View>
        )}

        {/* Meeting instructions / note for driver — helps when the pin is a
            big venue (mall, airport): "I'll be at the north entrance". */}
        <TextInput
          style={styles.noteInput}
          placeholder="Note for driver (e.g. which mall entrance) — optional"
          placeholderTextColor={colors.textDim}
          value={note}
          onChangeText={setNote}
          maxLength={200}
          multiline
        />

        {/* Confirm button */}
        <TouchableOpacity
          style={[styles.confirmBtn, (geocoding || checking) && styles.confirmBtnDisabled]}
          onPress={handleConfirm}
          disabled={geocoding || checking}
          activeOpacity={0.8}
          accessibilityRole="button"
          accessibilityLabel="Confirm pickup location"
        >
          <Text style={styles.confirmBtnText}>{checking ? 'Checking…' : 'Confirm pickup'}</Text>
          {!checking && <Ionicons name="arrow-forward" size={20} color="#FFF" />}
        </TouchableOpacity>
      </View>

      {/* Curated venue pickup-point chooser */}
      <Modal visible={!!chooser} transparent animationType="slide" onRequestClose={() => setChooser(null)}>
        <TouchableOpacity style={styles.chooserBackdrop} activeOpacity={1} onPress={() => setChooser(null)} />
        <View style={styles.chooserSheet}>
          <View style={styles.chooserHandle} />
          <Text style={styles.chooserTitle}>{chooser?.venue}</Text>
          <Text style={styles.chooserSubtitle}>Pick where to meet your driver — these are spots a car can reach.</Text>
          <ScrollView style={{ maxHeight: 320 }}>
            {(chooser?.points ?? []).map((p, i) => (
              <TouchableOpacity
                key={`${p.name}-${i}`}
                style={styles.chooserRow}
                activeOpacity={0.7}
                onPress={() => {
                  const label = `${chooser?.venue} — ${p.name}`;
                  setChooser(null);
                  proceed(p.lat, p.lng, label, note.trim() || p.name);
                }}
              >
                <Ionicons name="location-outline" size={20} color={colors.primary} />
                <Text style={styles.chooserRowText}>{p.name}</Text>
                <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
              </TouchableOpacity>
            ))}
            <TouchableOpacity
              style={[styles.chooserRow, { opacity: 0.8 }]}
              activeOpacity={0.7}
              onPress={() => { setChooser(null); proceed(region.latitude, region.longitude, address, note); }}
            >
              <Ionicons name="pin-outline" size={20} color={colors.textDim} />
              <Text style={[styles.chooserRowText, { color: colors.textDim }]}>Use my exact pin instead</Text>
            </TouchableOpacity>
          </ScrollView>
        </View>
      </Modal>
    </View>
  );
}

function createStyles(colors: ThemeColors, sf: (s: number) => number, insets: { bottom: number }) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: '#E8E8E8' },
    map: { flex: 1 },

    // ── Pin ──
    pinContainer: {
      position: 'absolute',
      top: '50%',
      left: '50%',
      marginLeft: -22,
      marginTop: -48,
      alignItems: 'center',
      zIndex: 10,
    },
    pinIcon: {
      textShadowColor: 'rgba(0,0,0,0.25)',
      textShadowOffset: { width: 0, height: 2 },
      textShadowRadius: 4,
    },
    pinShadow: {
      width: 10,
      height: 4,
      borderRadius: 5,
      backgroundColor: 'rgba(0,0,0,0.18)',
      marginTop: -2,
    },

    // ── Floating controls ──
    floatingBack: {
      position: 'absolute',
      left: 16,
      width: 44,
      height: 44,
      borderRadius: 22,
      backgroundColor: '#FFFFFF',
      justifyContent: 'center',
      alignItems: 'center',
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.15,
      shadowRadius: 6,
      elevation: 6,
      zIndex: 20,
    },
    titlePill: {
      position: 'absolute',
      alignSelf: 'center',
      backgroundColor: '#FFFFFF',
      paddingHorizontal: 18,
      paddingVertical: 8,
      borderRadius: 20,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.12,
      shadowRadius: 6,
      elevation: 6,
      zIndex: 20,
    },
    titlePillText: {
      fontSize: sf(14),
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
    },
    recenterBtn: {
      position: 'absolute',
      right: 16,
      width: 48,
      height: 48,
      borderRadius: 24,
      backgroundColor: '#FFFFFF',
      justifyContent: 'center',
      alignItems: 'center',
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.15,
      shadowRadius: 4,
      elevation: 4,
      zIndex: 20,
    },

    // ── Bottom card ──
    bottomCard: {
      position: 'absolute',
      bottom: 0,
      left: 0,
      right: 0,
      backgroundColor: colors.surface,
      borderTopLeftRadius: 24,
      borderTopRightRadius: 24,
      paddingHorizontal: 20,
      paddingTop: 18,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: -4 },
      shadowOpacity: 0.1,
      shadowRadius: 8,
      elevation: 12,
    },
    instruction: {
      fontSize: sf(13),
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
      textAlign: 'center',
      marginBottom: 14,
    },
    addressRow: {
      flexDirection: 'row',
      alignItems: 'center',
      marginBottom: 10,
      gap: 12,
    },
    addressDot: {
      width: 12,
      height: 12,
      borderRadius: 6,
      backgroundColor: '#10B981',
    },
    addressText: {
      fontSize: sf(15),
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.text,
      lineHeight: 20,
    },
    addressLoading: {
      fontSize: sf(14),
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
    },
    warningRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
      backgroundColor: '#FFFBEB',
      borderRadius: 10,
      paddingVertical: 8,
      paddingHorizontal: 12,
      marginBottom: 10,
      borderWidth: 1,
      borderColor: '#FDE68A',
    },
    warningText: {
      flex: 1,
      fontSize: sf(12),
      fontFamily: 'PlusJakartaSans_400Regular',
      color: '#92400E',
    },
    dropoffRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 12,
      marginBottom: 14,
      opacity: 0.6,
    },
    dropoffText: {
      flex: 1,
      fontSize: sf(13),
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
    },
    chooserBackdrop: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,0.5)' },
    chooserSheet: {
      position: 'absolute',
      bottom: 0,
      left: 0,
      right: 0,
      backgroundColor: colors.surface,
      borderTopLeftRadius: 24,
      borderTopRightRadius: 24,
      paddingHorizontal: 20,
      paddingTop: 10,
      paddingBottom: Math.max(insets.bottom, 16) + 8,
    },
    chooserHandle: { alignSelf: 'center', width: 40, height: 4, borderRadius: 2, backgroundColor: colors.border, marginBottom: 12 },
    chooserTitle: { fontSize: sf(18), fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.text },
    chooserSubtitle: { fontSize: sf(13), fontFamily: 'PlusJakartaSans_400Regular', color: colors.textDim, marginTop: 2, marginBottom: 12 },
    chooserRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 12,
      paddingVertical: 14,
      paddingHorizontal: 12,
      borderRadius: 12,
      backgroundColor: colors.surfaceLight,
      marginBottom: 8,
    },
    chooserRowText: { flex: 1, fontSize: sf(15), fontFamily: 'PlusJakartaSans_500Medium', color: colors.text },
    noteInput: {
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 12,
      paddingHorizontal: 12,
      paddingVertical: 10,
      minHeight: 44,
      maxHeight: 88,
      fontSize: sf(14),
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.text,
      marginBottom: 14,
      textAlignVertical: 'top',
    },
    confirmBtn: {
      flexDirection: 'row',
      backgroundColor: colors.primary,
      borderRadius: 28,
      paddingVertical: 16,
      alignItems: 'center',
      justifyContent: 'center',
      gap: 8,
    },
    confirmBtnDisabled: {
      opacity: 0.5,
    },
    confirmBtnText: {
      fontSize: sf(17),
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: '#FFFFFF',
    },
  });
}
