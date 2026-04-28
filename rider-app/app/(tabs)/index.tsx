import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  useWindowDimensions,
  ActivityIndicator,
  Platform,
  Image,
  Linking,
  AppState,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useFocusEffect } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import * as Location from 'expo-location';
import Constants from 'expo-constants';
import { useAuthStore } from '@shared/store/authStore';
import { DEFAULT_LATITUDE, DEFAULT_LONGITUDE } from '../../constants/geo';
import { useRideStore } from '../../store/rideStore';
import AppMap from '@shared/components/AppMap';
import CustomAlert from '@shared/components/CustomAlert';
import { SOSButton } from '@shared/components/SOSButton';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

const HOME_DATA_TTL_MS = 5 * 60 * 1000; // 5 minutes

const getBackendUrl = () => {
  if (process.env.EXPO_PUBLIC_BACKEND_URL) return process.env.EXPO_PUBLIC_BACKEND_URL;
  if (process.env.EXPO_PUBLIC_API_URL) return process.env.EXPO_PUBLIC_API_URL;
  if (Constants.expoConfig?.hostUri) {
    const host = Constants.expoConfig.hostUri.split(':')[0];
    return `http://${host}:8000`;
  }
  return '';
};

export default function HomeScreen() {
  const router = useRouter();
  const { user } = useAuthStore();
  const { savedAddresses, fetchSavedAddresses, setUserLocation, currentRide, triggerEmergency } = useRideStore();
  const [showPromo, setShowPromo] = useState(true);
  const [location, setLocation] = useState<any>(null);
  const [region, setRegion] = useState<any>(null);
  const [temperature, setTemperature] = useState<number | null>(null);
  const [alertState, setAlertState] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
  }>({ visible: false, title: '', message: '', variant: 'info' });
  const mapRef = useRef<any>(null);
  const lastFetchedAt = useRef<number>(0);

  const { width, height } = useWindowDimensions();
  const { colors, isDark } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  // Save/load last location from AsyncStorage for instant map on cold start
  const saveLastLocation = async (lat: number, lng: number) => {
    try {
      const AsyncStorage = require('@react-native-async-storage/async-storage').default;
      await AsyncStorage.setItem('spinr_last_location', JSON.stringify({ lat, lng }));
    } catch {}
  };

  const loadLastLocation = async () => {
    try {
      const AsyncStorage = require('@react-native-async-storage/async-storage').default;
      const saved = await AsyncStorage.getItem('spinr_last_location');
      if (saved) return JSON.parse(saved);
    } catch {}
    return null;
  };

  // Fresh GPS fix + weather. Location is NOT TTL-gated — we always want a
  // current fix when the user is looking at the map. Cache only primes the
  // very first paint.
  const refreshLocation = useCallback(async (useCache: boolean) => {
    if (useCache) {
      const cached = await loadLastLocation();
      if (cached) {
        const cachedLoc = { coords: { latitude: cached.lat, longitude: cached.lng } };
        setLocation(cachedLoc);
        setUserLocation({ latitude: cached.lat, longitude: cached.lng });
      }
    }

    const { status } = await Location.requestForegroundPermissionsAsync();
    if (status !== 'granted') return;

    if (useCache) {
      try {
        const lastKnown = await Location.getLastKnownPositionAsync();
        if (lastKnown) {
          setLocation(lastKnown);
          setUserLocation({ latitude: lastKnown.coords.latitude, longitude: lastKnown.coords.longitude });
          saveLastLocation(lastKnown.coords.latitude, lastKnown.coords.longitude);
        }
      } catch {}
    }

    Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced })
      .then(loc => {
        setLocation(loc);
        setUserLocation({ latitude: loc.coords.latitude, longitude: loc.coords.longitude });
        saveLastLocation(loc.coords.latitude, loc.coords.longitude);

        fetch(`https://api.open-meteo.com/v1/forecast?latitude=${loc.coords.latitude}&longitude=${loc.coords.longitude}&current_weather=true`)
          .then(r => r.json())
          .then(data => {
            if (data?.current_weather?.temperature !== undefined) {
              setTemperature(Math.round(data.current_weather.temperature));
            }
          })
          .catch(() => {});
      })
      .catch(() => {});
  }, [setUserLocation]);

  // Saved-places can be TTL-gated — they rarely change within a session.
  const fetchHomeData = useCallback(async () => {
    await refreshLocation(true);
    fetchSavedAddresses();
  }, [refreshLocation, fetchSavedAddresses]);

  useFocusEffect(
    useCallback(() => {
      const now = Date.now();
      if (now - lastFetchedAt.current < HOME_DATA_TTL_MS) {
        // Still refresh the location so a stale fix from a previous session
        // (or from before a long commute) gets replaced. Skip the saved-
        // places fetch — those don't change minute-to-minute.
        refreshLocation(false);
        return;
      }
      lastFetchedAt.current = now;
      fetchHomeData();
    }, [fetchHomeData, refreshLocation])
  );

  // App resume — always grab a fresh fix, no cache. `initialRegion` on
  // AppMap is one-shot, so we also re-center the map below when location
  // changes post-mount.
  useEffect(() => {
    const sub = AppState.addEventListener('change', (next) => {
      if (next === 'active') refreshLocation(false);
    });
    return () => sub.remove();
  }, [refreshLocation]);

  // Re-center the map when a fresh GPS fix lands after mount. `initialRegion`
  // only applies on first render, so without this the map stays pinned to
  // whatever (often cached) location was first set.
  const hasCenteredRef = useRef(false);
  const regionRef = useRef(region);
  useEffect(() => { regionRef.current = region; }, [region]);
  useEffect(() => {
    if (!location || !mapRef.current) return;
    if (!hasCenteredRef.current) {
      // First location sets initialRegion; no need to animate.
      hasCenteredRef.current = true;
      return;
    }
    mapRef.current.animateToRegion?.({
      latitude: location.coords.latitude,
      longitude: location.coords.longitude,
      latitudeDelta: regionRef.current?.latitudeDelta ?? 0.0922,
      longitudeDelta: regionRef.current?.longitudeDelta ?? 0.0421,
    }, 400);
  }, [location]);

  const getGreeting = () => {
    const hour = new Date().getHours();
    if (hour < 12) return 'GOOD MORNING';
    if (hour < 17) return 'GOOD AFTERNOON';
    return 'GOOD EVENING';
  };

  const handleSearchPress = () => {
    router.push('/search-destination');
  };

  const handleQuickAction = (type: string) => {
    // Navigate to search with pre-selected type
    router.push('/search-destination');
  };

  return (
    <View style={styles.container}>
      {/* Map Implementation */}
      <View style={styles.mapContainer}>
        {/* Header */}
        <SafeAreaView edges={['top']} style={styles.headerSafeArea}>
          <View style={styles.header}>
            <View style={styles.headerLeft}>
              <View style={styles.avatarContainer}>
                {user?.profile_image ? (
                  <Image
                    source={{ uri: user.profile_image }}
                    style={styles.avatarImage}
                  />
                ) : (
                  <Ionicons name="person" size={20} color={colors.textDim} />
                )}
              </View>
              <View style={styles.greetingContainer}>
                <View style={styles.greetingRow}>
                  <Text style={styles.greetingText}>
                    {getGreeting()}
                  </Text>
                  {temperature !== null && (
                    <Text style={styles.temperatureText}> · {temperature}°C</Text>
                  )}
                </View>
                {user?.first_name && (
                  <Text style={styles.cityText}>{user.first_name}</Text>
                )}
              </View>
            </View>
            <TouchableOpacity
              style={styles.notificationButton}
              accessibilityLabel="Notifications"
              accessibilityRole="button"
            >
              <Ionicons name="notifications-outline" size={24} color={colors.text} />
            </TouchableOpacity>
          </View>
        </SafeAreaView>

        {/* Map View */}
        {/* Map View */}
        {location ? (
          <AppMap
            ref={mapRef}
            style={StyleSheet.absoluteFill}
            initialRegion={{
              latitude: location.coords.latitude,
              longitude: location.coords.longitude,
              latitudeDelta: 0.0922,
              longitudeDelta: 0.0421,
            }}
            showsUserLocation={true}
            userInterfaceStyle={isDark ? "dark" : "light"}
            onRegionChangeComplete={setRegion}
          />
        ) : (
          <View style={styles.mapPlaceholder}>
            {location ? (
              <ActivityIndicator size="large" color={colors.primary} />
            ) : (
              <View style={styles.mapOverlay}>
                <Ionicons name="location" size={40} color={colors.primary} />
                <Text style={styles.mapText}>Locating...</Text>
              </View>
            )}
          </View>
        )}

        {/* Map Controls Container - Right Side */}
        {/* Map Controls Container - Right Side */}
        <View style={styles.mapControls}>
          <TouchableOpacity style={styles.mapControlButton} onPress={() => {
            if (region && mapRef.current) {
              mapRef.current.animateToRegion({
                ...region,
                latitudeDelta: region.latitudeDelta / 2,
                longitudeDelta: region.longitudeDelta / 2,
              }, 500);
            }
          }}>
            <Ionicons name="add" size={24} color={colors.text} />
          </TouchableOpacity>
          <View style={styles.divider} />
          <TouchableOpacity style={styles.mapControlButton} onPress={() => {
            if (region && mapRef.current) {
              mapRef.current.animateToRegion({
                ...region,
                latitudeDelta: region.latitudeDelta * 2,
                longitudeDelta: region.longitudeDelta * 2,
              }, 500);
            }
          }}>
            <Ionicons name="remove" size={24} color={colors.text} />
          </TouchableOpacity>
        </View>

        {/* SOS Button - Left Side */}
        <View style={styles.sosButton}>
          <SOSButton
            rideId={currentRide?.id}
            onTrigger={async (rideId, lat, lng) => {
              if (rideId) {
                await triggerEmergency(rideId, lat, lng);
              } else {
                Linking.openURL('tel:911');
              }
            }}
            size="small"
          />
        </View>

        {/* Current Location Button — always fetches a fresh fix. Tapping
            this is an explicit user request for "where am I now", so we
            bypass any cached state and hit getCurrentPositionAsync. */}
        <TouchableOpacity style={styles.locationButton} onPress={async () => {
          if (!mapRef.current) return;
          const { status } = await Location.requestForegroundPermissionsAsync();
          if (status !== 'granted') return;
          let loc: any;
          try {
            loc = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
          } catch (e) {
            console.warn('Could not get current location, using fallback:', e);
            loc = location ?? { coords: { latitude: DEFAULT_LATITUDE, longitude: DEFAULT_LONGITUDE } };
          }
          setLocation(loc);
          setUserLocation({ latitude: loc.coords.latitude, longitude: loc.coords.longitude });
          saveLastLocation(loc.coords.latitude, loc.coords.longitude);
          mapRef.current.animateToRegion({
            latitude: loc.coords.latitude,
            longitude: loc.coords.longitude,
            latitudeDelta: 0.01,
            longitudeDelta: 0.01,
          }, 1000);
        }}>
          <Ionicons name="locate" size={24} color={colors.text} />
        </TouchableOpacity>
      </View>

      {/* Bottom Sheet */}
      <View style={styles.bottomSheet}>
        <View style={styles.sheetHandle} />

        {/* Search Bar + AI Button */}
        <View style={styles.searchRow}>
          <TouchableOpacity
            style={styles.searchBar}
            onPress={handleSearchPress}
            accessibilityLabel="Where to? Search for a destination"
            accessibilityRole="button"
            accessibilityHint="Opens the destination search screen"
          >
            <Ionicons name="search" size={22} color={colors.primary} />
            <Text style={styles.searchPlaceholder}>Where to?</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.aiButton}
            onPress={() => {
              // Coming soon
              setAlertState({
                visible: true,
                title: 'Coming Soon',
                message: 'AI Ride Booking is coming soon! Book rides by just talking or texting.',
                variant: 'info',
              });
            }}
            activeOpacity={0.8}
            accessibilityLabel="AI ride booking"
            accessibilityRole="button"
            accessibilityHint="AI-powered booking assistant, coming soon"
          >
            <View style={styles.aiIconGlow} />
            <Ionicons name="sparkles" size={20} color="#FFFFFF" />
            <Text style={styles.aiButtonText}>AI</Text>
          </TouchableOpacity>
        </View>

        {/* Quick Actions */}
        <View style={styles.quickActions}>
          <TouchableOpacity style={styles.quickAction} onPress={() => handleQuickAction('home')}>
            <View style={styles.quickActionIcon}>
              <Ionicons name="home" size={22} color={colors.primary} />
            </View>
            <Text style={styles.quickActionText}>Home</Text>
          </TouchableOpacity>

          <TouchableOpacity style={styles.quickAction} onPress={() => handleQuickAction('work')}>
            <View style={styles.quickActionIcon}>
              <Ionicons name="briefcase" size={22} color={colors.primary} />
            </View>
            <Text style={styles.quickActionText}>Work</Text>
          </TouchableOpacity>

          <TouchableOpacity style={styles.quickAction} onPress={() => handleQuickAction('saved')}>
            <View style={styles.quickActionIcon}>
              <Ionicons name="star" size={22} color={colors.primary} />
            </View>
            <Text style={styles.quickActionText}>Saved</Text>
          </TouchableOpacity>
        </View>

        {/* Promo Banner */}
        {showPromo && (
          <View style={styles.promoBanner}>
            <View style={styles.promoIconContainer}>
              <Ionicons name="megaphone" size={20} color={colors.primary} />
            </View>
            <View style={styles.promoContent}>
              <Text style={styles.promoTitle}>Ride local. Support local.</Text>
              <Text style={styles.promoText}>
                We take 0% commission. 100% of{"\n"}your fare goes to your driver.
              </Text>
            </View>
            <TouchableOpacity onPress={() => setShowPromo(false)} style={styles.promoClose}>
              <Ionicons name="close" size={20} color={colors.textDim} />
            </TouchableOpacity>
          </View>
        )}
      </View>
      <CustomAlert
        visible={alertState.visible}
        title={alertState.title}
        message={alertState.message}
        variant={alertState.variant}
        buttons={[{ text: 'OK', style: 'default' }]}
        onClose={() => setAlertState(prev => ({ ...prev, visible: false }))}
      />
    </View>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
      flex: 1,
      backgroundColor: colors.background,
    },
    mapContainer: {
      flex: 1,
      position: 'relative',
    },
    headerSafeArea: {
      position: 'absolute',
      top: 0,
      left: 0,
      right: 0,
      zIndex: 10,
    },
    header: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'center',
      paddingHorizontal: 20,
      paddingVertical: 12,
    },
    headerLeft: {
      flexDirection: 'row',
      alignItems: 'center',
    },
    avatarContainer: {
      width: 44,
      height: 44,
      borderRadius: 22,
      backgroundColor: '#D4C4A8',
      justifyContent: 'center',
      alignItems: 'center',
      overflow: 'hidden',
    },
    avatarImage: {
      width: 44,
      height: 44,
      borderRadius: 22,
    },
    greetingContainer: {
      marginLeft: 12,
    },
    greetingRow: {
      flexDirection: 'row',
      alignItems: 'center',
    },
    greetingText: {
      fontSize: 11,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.textDim,
      letterSpacing: 1,
    },
    temperatureText: {
      fontSize: 11,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.textSecondary,
      letterSpacing: 0.5,
    },
    cityText: {
      fontSize: 18,
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text,
    },
    notificationButton: {
      width: 44,
      height: 44,
      borderRadius: 22,
      backgroundColor: colors.surface,
      justifyContent: 'center',
      alignItems: 'center',
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.1,
      shadowRadius: 4,
      elevation: 3,
    },
    mapPlaceholder: {
      flex: 1,
      backgroundColor: '#E0E7E0',
      justifyContent: 'center',
      alignItems: 'center',
    },
    mapOverlay: {
      alignItems: 'center',
      opacity: 0.5,
    },
    mapText: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.textDim,
      marginTop: 8,
    },
    carMarker: {
      position: 'absolute',
      width: 28,
      height: 28,
      borderRadius: 14,
      backgroundColor: colors.primary,
      justifyContent: 'center',
      alignItems: 'center',
    },
    locationButton: {
      position: 'absolute',
      right: 20,
      bottom: 20,
      width: 48,
      height: 48,
      borderRadius: 24,
      backgroundColor: colors.surface,
      justifyContent: 'center',
      alignItems: 'center',
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.15,
      elevation: 4,
    },
    mapControls: {
      position: 'absolute',
      right: 20,
      bottom: 80, // Above location button
      backgroundColor: colors.surface,
      borderRadius: 12,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.15,
      shadowRadius: 6,
      elevation: 4,
      padding: 4,
    },
    mapControlButton: {
      width: 44,
      height: 44,
      justifyContent: 'center',
      alignItems: 'center',
    },
    divider: {
      height: 1,
      backgroundColor: colors.border,
      marginHorizontal: 4,
    },
    sosButton: {
      position: 'absolute',
      left: 20,
      bottom: 20,
    },
    bottomSheet: {
      backgroundColor: colors.surface,
      borderTopLeftRadius: 24,
      borderTopRightRadius: 24,
      paddingHorizontal: 20,
      paddingTop: 12,
      paddingBottom: 20,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: -4 },
      shadowOpacity: 0.1,
      shadowRadius: 12,
      elevation: 8,
    },
    sheetHandle: {
      width: 40,
      height: 4,
      backgroundColor: colors.border,
      borderRadius: 2,
      alignSelf: 'center',
      marginBottom: 16,
    },
    searchRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 10,
      marginBottom: 20,
    },
    searchBar: {
      flex: 1,
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: colors.surfaceLight,
      borderRadius: 28,
      paddingHorizontal: 20,
      paddingVertical: 16,
    },
    searchPlaceholder: {
      fontSize: 16,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.textDim,
      marginLeft: 12,
    },
    aiButton: {
      width: 56,
      height: 56,
      borderRadius: 28,
      backgroundColor: colors.primary,
      alignItems: 'center',
      justifyContent: 'center',
      elevation: 4,
      shadowColor: colors.primary,
      shadowOffset: { width: 0, height: 4 },
      shadowOpacity: 0.35,
      shadowRadius: 8,
      overflow: 'hidden',
    },
    aiIconGlow: {
      position: 'absolute',
      width: 30,
      height: 30,
      borderRadius: 15,
      backgroundColor: 'rgba(255,255,255,0.2)',
      top: 4,
      left: 4,
    },
    aiButtonText: {
      color: '#FFFFFF',
      fontSize: 10,
      fontWeight: '800',
      letterSpacing: 0.5,
      marginTop: 1,
    },
    quickActions: {
      flexDirection: 'row',
      justifyContent: 'space-around',
      marginBottom: 20,
    },
    quickAction: {
      alignItems: 'center',
    },
    quickActionIcon: {
      width: 56,
      height: 56,
      borderRadius: 28,
      backgroundColor: '#FFF0F0',
      justifyContent: 'center',
      alignItems: 'center',
      marginBottom: 8,
    },
    quickActionText: {
      fontSize: 13,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.text,
    },
    promoBanner: {
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: '#FFF8F0',
      borderRadius: 16,
      padding: 16,
    },
    promoIconContainer: {
      width: 40,
      height: 40,
      borderRadius: 20,
      backgroundColor: '#FFE8E8',
      justifyContent: 'center',
      alignItems: 'center',
    },
    promoContent: {
      flex: 1,
      marginLeft: 12,
    },
    promoTitle: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text,
      marginBottom: 2,
    },
    promoText: {
      fontSize: 12,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
      lineHeight: 18,
    },
    promoClose: {
      padding: 4,
    },
  });
}
