import React, { useState, useEffect, useRef, useMemo, useCallback, useContext } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  useWindowDimensions,
  Platform,
  Image,
  Linking,
  AppState,
} from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useFocusEffect } from 'expo-router/react-navigation';
import { Ionicons } from '@expo/vector-icons';
import * as Location from 'expo-location';
import BottomSheet, { BottomSheetScrollView } from '@gorhom/bottom-sheet';
import { useAuthStore } from '@shared/store/authStore';
import { useExitOnBackPress } from '@shared/hooks/useExitOnBackPress';
import api, { isAppCheckTokenReady } from '@shared/api/client';
import { useRideStore } from '../../store/rideStore';
import { useBottomSheetGuard } from '../../hooks/useBottomSheetGuard';
import { useAiChatStore } from '../../store/aiChatStore';
import AppMap from '@shared/components/AppMap';
import { CarMarker, resolveMarkerVariant } from '@shared/components/CarMarker';
import { useVehicleTypeStore } from '@shared/store/vehicleTypeStore';
import { showToast } from '../../store/toastStore';
import { RiderSOS } from '../../components/RiderSOS';
import { RidelessSosEnabledContext } from '../_layout';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import {
  checkNotificationPermission,
  requestNotificationPermission,
  openNotificationSettings,
} from '@shared/services/firebase';
import { useTranslation } from '../../i18n';

const HOME_DATA_TTL_MS = 5 * 60 * 1000;

// Home promo banner messages. Add / edit / reorder entries here — the banner
// rotates through them automatically (and shows dots when there's more than
// one). `icon` is any Ionicons name. Keep `title` short and `text` to ~2 lines.
type PromoMessage = { icon: keyof typeof Ionicons.glyphMap; title: string; text: string };
const PROMO_MESSAGES: PromoMessage[] = [
  {
    icon: 'megaphone',
    title: 'Ride local. Support local.',
    text: 'Spinr takes no cut — 100% of\nyour fare goes to your driver.',
  },
  {
    icon: 'leaf',
    title: 'Saskatchewan-first',
    text: 'Built for Saskatchewan, run in\nSaskatchewan — your fares stay home.',
  },
  {
    icon: 'shield-checkmark',
    title: 'Safety built in',
    text: 'One-tap SOS, trip sharing, and\nverified drivers on every ride.',
  },
];
const PROMO_ROTATE_MS = 6000;

export default function HomeScreen() {
  const router = useRouter();
  const { user } = useAuthStore();
  const { fetchSavedAddresses, setUserLocation, currentRide, triggerEmergency, triggerRidelessEmergency, fetchActiveRide } = useRideStore();
  const ridelessSosEnabled = useContext(RidelessSosEnabledContext);
  const { t } = useTranslation();

  // Home is a navigation root: the Android back button must background the app,
  // not pop back into the login/OTP screens that sit beneath it in history.
  useExitOnBackPress();
  const aiEnabled = useAiChatStore((s) => s.enabled);
  const aiMode = useAiChatStore((s) => s.mode);
  const loadAiConfig = useAiChatStore((s) => s.loadConfig);
  useEffect(() => {
    loadAiConfig();
  }, [loadAiConfig]);
  const [unreadNotifCount, setUnreadNotifCount] = useState(0);
  const [showPromo, setShowPromo] = useState(true);
  const [notifPermissionGranted, setNotifPermissionGranted] = useState(true);
  const [dismissedNotifBanner, setDismissedNotifBanner] = useState(false);

  useFocusEffect(
    useCallback(() => {
      let active = true;
      checkNotificationPermission().then((res) => {
        if (active) setNotifPermissionGranted(res.granted);
      }).catch(() => {});
      return () => { active = false; };
    }, [])
  );

  const handleEnableNotifications = async () => {
    const granted = await requestNotificationPermission();
    if (granted) {
      setNotifPermissionGranted(true);
    } else {
      showToast('Permission Required', 'Notification permissions are disabled in device settings.', 'warning');
      await openNotificationSettings();
    }
  };

  const [location, setLocation] = useState<any>(null);
  const [region, setRegion] = useState<any>(null);
  const [temperature, setTemperature] = useState<number | null>(null);
  const [nearbyDrivers, setNearbyDrivers] = useState<{ id: string; lat: number; lng: number; heading?: number; vehicle_type_id?: string; vehicle_type_name?: string; marker_variant?: string }[]>([]);
  // Vehicle type config (marker variant + custom marker image), synced once
  // per app open by the root layout — resolves every driver's marker without
  // per-driver requests.
  const vehicleTypesById = useVehicleTypeStore((s) => s.byId);

  const mapRef = useRef<any>(null);
  const bottomSheetRef = useRef<BottomSheet>(null);
  // The sheet must never be off-screen on Home, but 0-height layout races
  // (cold start, tab detach/re-attach) can silently resolve it to "closed".
  // The guard hook snaps it back — see useBottomSheetGuard for the full story.
  const { handleSheetChange, handleContainerLayout } = useBottomSheetGuard(bottomSheetRef);
  const lastFetchedAt = useRef<number>(0);
  const snapPoints = useMemo(() => ['28%', '45%'], []);

  const { width } = useWindowDimensions();
  const isTablet = width >= 768;
  const { colors, isDark } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const saveLastLocation = async (lat: number, lng: number) => {
    try {
      await AsyncStorage.setItem('spinr_last_location', JSON.stringify({ lat, lng }));
    } catch {}
  };

  const loadLastLocation = async () => {
    try {
      const saved = await AsyncStorage.getItem('spinr_last_location');
      if (saved) return JSON.parse(saved);
    } catch {}
    return null;
  };

  const refreshLocation = useCallback(async (useCache: boolean) => {
    if (useCache) {
      const cached = await loadLastLocation();
      if (cached) {
        setLocation({ coords: { latitude: cached.lat, longitude: cached.lng } });
      }
    }

    let { status } = await Location.getForegroundPermissionsAsync();
    if (status !== 'granted') {
      const res = await Location.requestForegroundPermissionsAsync();
      status = res.status;
    }
    if (status !== 'granted') {
      showToast('Location Required', 'Enable location in Settings to use Spinr.', 'warning');
      // Linking.openSettings() is native-only (no react-native-web
      // implementation) — same guard as firebase.ts's openNotificationSettings.
      try {
        if (Platform.OS === 'ios') {
          await Linking.openURL('app-settings:');
        } else {
          await Linking.openSettings();
        }
      } catch (e) {
        console.log('[Location] Failed to open settings:', e);
      }
      return;
    }

    if (useCache) {
      try {
        const lastKnown = await Location.getLastKnownPositionAsync();
        if (lastKnown) {
          setLocation(lastKnown);
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

  const fetchHomeData = useCallback(async () => {
    await refreshLocation(true);
    fetchSavedAddresses();
    // /api/v1/notifications is App-Check-enforced in prod; polling before the
    // App Check token is minted 401s. Skip until ready — useFocusEffect re-runs
    // this, so the badge loads on the next focus once App Check is up.
    if (await isAppCheckTokenReady()) {
      api.get('/notifications?limit=1').then((res: any) => {
        setUnreadNotifCount(res.data?.unread_count ?? 0);
      }).catch(() => {});
    }
  }, [refreshLocation, fetchSavedAddresses]);

  useFocusEffect(
    useCallback(() => {
      let cancelled = false;

      (async () => {
        const result = await fetchActiveRide();
        if (cancelled || !result?.active || !result.ride) return;
        const s = result.ride.status;
        if (s === 'searching' || s === 'driver_assigned' || s === 'driver_accepted') {
          router.push({ pathname: '/driver-arriving', params: { rideId: result.ride.id } } as any);
        } else if (s === 'driver_arrived') {
          router.push({ pathname: '/driver-arrived', params: { rideId: result.ride.id } } as any);
        } else if (s === 'in_progress') {
          router.push({ pathname: '/ride-in-progress', params: { rideId: result.ride.id } } as any);
        } else if (s === 'completed') {
          const ps = result.ride.payment_status;
          if (ps !== 'paid' && ps !== 'waived_admin') {
            router.push({ pathname: '/ride-completed', params: { rideId: result.ride.id } } as any);
          }
        }
      })();

      const now = Date.now();
      if (now - lastFetchedAt.current < HOME_DATA_TTL_MS) {
        refreshLocation(false);
      } else {
        lastFetchedAt.current = now;
        fetchHomeData();
      }
      return () => { cancelled = true; };
    }, [fetchHomeData, refreshLocation, fetchActiveRide, router])
  );

  useEffect(() => {
    const sub = AppState.addEventListener('change', (next) => {
      if (next === 'active') {
        refreshLocation(false).catch(() => {});
      }
    });
    return () => sub.remove();
  }, [refreshLocation]);

  useEffect(() => {
    if (!location) return;
    const fetchDrivers = () => {
      const { latitude, longitude } = location.coords;
      api.get(`/drivers/nearby?lat=${latitude}&lng=${longitude}`)
        .then((res: any) => {
          const drivers = (res.data || []).map((d: any) => ({
            id: d.id,
            lat: d.lat,
            lng: d.lng,
            heading: d.heading ?? undefined,
            vehicle_type_id: d.vehicle_type_id ?? undefined,
            vehicle_type_name: d.vehicle_type_name ?? undefined,
            marker_variant: d.marker_variant ?? undefined,
          }));
          setNearbyDrivers(drivers);
        })
        .catch(() => {});
    };
    fetchDrivers();
    const interval = setInterval(fetchDrivers, 15000);
    return () => clearInterval(interval);
  }, [location]);

  // Two drivers at (nearly) the same point — common when several cars idle
  // at one lot, or when testing with phones side-by-side — render as a single
  // overlapping marker. Spread co-located drivers onto a small ring (~8 m) so
  // each car is individually visible, and supply a deterministic heading
  // fallback when the backend has no heading yet so they don't all point the
  // same way. Deterministic (index-based, not Math.random) so markers don't
  // jitter on every re-render.
  const displayDrivers = useMemo(() => {
    const PRECISION = 1e4; // ~11 m grid — drivers within this bucket collide
    const RING_METERS = 8;
    const groups = new Map<string, typeof nearbyDrivers>();
    for (const d of nearbyDrivers) {
      const key = `${Math.round(d.lat * PRECISION)}:${Math.round(d.lng * PRECISION)}`;
      const g = groups.get(key);
      if (g) g.push(d); else groups.set(key, [d]);
    }
    const out: { id: string; lat: number; lng: number; heading: number; vehicle_type_id?: string; vehicle_type_name?: string; marker_variant?: string }[] = [];
    for (const group of groups.values()) {
      group.forEach((d, i) => {
        let { lat, lng } = d;
        if (group.length > 1) {
          const angle = (2 * Math.PI * i) / group.length;
          const dLat = (RING_METERS / 111320) * Math.cos(angle);
          const dLng = (RING_METERS / (111320 * Math.cos((lat * Math.PI) / 180))) * Math.sin(angle);
          lat += dLat;
          lng += dLng;
        }
        const heading = d.heading ?? (group.length > 1 ? (360 / group.length) * i : 0);
        out.push({ id: d.id, lat, lng, heading, vehicle_type_id: d.vehicle_type_id, vehicle_type_name: d.vehicle_type_name, marker_variant: d.marker_variant });
      });
    }
    return out;
  }, [nearbyDrivers]);

  const hasCenteredRef = useRef(false);
  const regionRef = useRef(region);
  useEffect(() => { regionRef.current = region; }, [region]);
  useEffect(() => {
    if (!location || !mapRef.current) return;
    if (!hasCenteredRef.current) {
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
    router.push('/search-destination' as any);
  };

  const handleAiPress = () => {
    if (aiEnabled) {
      router.push('/ai-assistant' as any);
    } else {
      showToast('Coming Soon', 'AI Ride Booking is coming soon!', 'info');
    }
  };

  const handleQuickAction = (_type: string) => {
    router.push('/search-destination' as any);
  };

  const handleLocationPress = async () => {
    if (!mapRef.current) return;
    let { status } = await Location.getForegroundPermissionsAsync();
    if (status !== 'granted') {
      const res = await Location.requestForegroundPermissionsAsync();
      status = res.status;
    }
    if (status !== 'granted') {
      showToast('Location Required', 'Enable location in Settings to use Spinr.', 'warning');
      // Linking.openSettings() is native-only (no react-native-web
      // implementation) — same guard as firebase.ts's openNotificationSettings.
      try {
        if (Platform.OS === 'ios') {
          await Linking.openURL('app-settings:');
        } else {
          await Linking.openSettings();
        }
      } catch (e) {
        console.log('[Location] Failed to open settings:', e);
      }
      return;
    }
    let loc: any;
    try {
      loc = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
    } catch {
      if (!location) return;
      mapRef.current.animateToRegion({
        latitude: location.coords.latitude,
        longitude: location.coords.longitude,
        latitudeDelta: 0.01,
        longitudeDelta: 0.01,
      }, 1000);
      return;
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
  };

  return (
    <View style={[styles.container, isTablet && { flexDirection: 'row' as const }]} onLayout={handleContainerLayout}>
      <View style={styles.mapContainer}>
        <SafeAreaView edges={['top']} style={styles.headerSafeArea}>
          <View style={styles.header}>
            <View style={styles.headerLeft}>
              <View style={styles.avatarContainer}>
                {user?.profile_image ? (
                  <Image source={{ uri: user.profile_image }} style={styles.avatarImage} />
                ) : (
                  <Ionicons name="person" size={20} color={colors.textDim} />
                )}
              </View>
              <View style={styles.greetingContainer}>
                <View style={styles.greetingRow}>
                  <Text style={styles.greetingText}>{getGreeting()}</Text>
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
              onPress={() => router.push('/notifications' as any)}
              accessibilityLabel={unreadNotifCount > 0 ? `Notifications, ${unreadNotifCount} unread` : 'Notifications'}
              accessibilityRole="button"
              hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
            >
              <Ionicons name="notifications-outline" size={24} color={colors.text} />
              {unreadNotifCount > 0 && (
                <View style={styles.notifBadge}>
                  <Text style={styles.notifBadgeText}>
                    {unreadNotifCount > 9 ? '9+' : unreadNotifCount}
                  </Text>
                </View>
              )}
            </TouchableOpacity>
          </View>

          {!notifPermissionGranted && !dismissedNotifBanner && (
            <View style={styles.notifBanner}>
              <View style={styles.notifBannerLeft}>
                <View style={styles.notifBannerIconBg}>
                  <Ionicons name="notifications" size={20} color="#F59E0B" />
                </View>
                <View style={styles.notifBannerTextContainer}>
                  <Text style={styles.notifBannerTitle}>Turn on notifications</Text>
                  <Text style={styles.notifBannerSub}>Get updates on driver arrival and trip progress</Text>
                </View>
              </View>
              <View style={styles.notifBannerActions}>
                <TouchableOpacity style={styles.notifEnableBtn} onPress={handleEnableNotifications}>
                  <Text style={styles.notifEnableBtnText}>Enable</Text>
                </TouchableOpacity>
                <TouchableOpacity style={styles.notifDismissBtn} onPress={() => setDismissedNotifBanner(true)}>
                  <Ionicons name="close" size={18} color={colors.textDim} />
                </TouchableOpacity>
              </View>
            </View>
          )}
        </SafeAreaView>

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
            showsMyLocationButton={false}
            toolbarEnabled={false}
            userInterfaceStyle={isDark ? 'dark' : 'light'}
            onRegionChangeComplete={setRegion}
          >
            {displayDrivers.map((driver) => {
              const vt = vehicleTypesById[driver.vehicle_type_id ?? ''];
              return (
                <CarMarker
                  key={driver.id}
                  identifier={`nearby-${driver.id}`}
                  coordinate={{ latitude: driver.lat, longitude: driver.lng }}
                  heading={driver.heading}
                  imageUri={vt?.marker_image_url}
                  variant={resolveMarkerVariant(
                    vt?.marker_variant ?? driver.marker_variant,
                    vt?.name ?? driver.vehicle_type_name,
                  )}
                  size={32}
                />
              );
            })}
          </AppMap>
        ) : (
          <View style={styles.mapPlaceholder}>
            <View style={styles.mapOverlay}>
              <Ionicons name="location" size={40} color={colors.primary} />
              <Text style={styles.mapText}>Locating...</Text>
            </View>
          </View>
        )}

        <View style={styles.mapControlsWrap}>
          <View style={styles.mapControls}>
            <TouchableOpacity
              style={styles.mapControlButton}
              onPress={() => {
                if (region && mapRef.current) {
                  mapRef.current.animateToRegion({
                    ...region,
                    latitudeDelta: region.latitudeDelta / 2,
                    longitudeDelta: region.longitudeDelta / 2,
                  }, 500);
                }
              }}
              accessibilityRole="button"
              accessibilityLabel="Zoom in"
              hitSlop={{ top: 6, bottom: 6, left: 6, right: 6 }}
            >
              <Ionicons name="add" size={24} color={colors.text} />
            </TouchableOpacity>
            <View style={styles.divider} />
            <TouchableOpacity
              style={styles.mapControlButton}
              onPress={() => {
                if (region && mapRef.current) {
                  mapRef.current.animateToRegion({
                    ...region,
                    latitudeDelta: region.latitudeDelta * 2,
                    longitudeDelta: region.longitudeDelta * 2,
                  }, 500);
                }
              }}
              accessibilityRole="button"
              accessibilityLabel="Zoom out"
              hitSlop={{ top: 6, bottom: 6, left: 6, right: 6 }}
            >
              <Ionicons name="remove" size={24} color={colors.text} />
            </TouchableOpacity>
          </View>

          <TouchableOpacity
            style={styles.locationButton}
            accessibilityRole="button"
            accessibilityLabel="Go to my location"
            hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
            onPress={handleLocationPress}
          >
            <Ionicons name="locate" size={24} color={colors.text} />
          </TouchableOpacity>
        </View>

        <View style={styles.sosButton}>
          {/* onTrigger is only ever invoked with a real rideId -- SOSButton
              guards the no-ride case itself and shows its own "No Active
              Ride" prompt with a 911 option the rider must confirm. This
              used to carry an `else { Linking.openURL('tel:911') }` branch
              that was unreachable for that reason, but would have become an
              UNPROMPTED 911 dial the moment anyone made SOSButton call
              onTrigger without a rideId. domain-safety.md's hardest rule is
              "Never auto-dial 911" (wrong-PSAP routing wastes seconds), so
              that branch was removed rather than left as a trap.

              Ride-less SOS (ACTION_ITEMS.md B15(c)): ridelessSosEnabled is
              read from AppSettings.rideless_sos_enabled (dark-launched,
              default off) via RidelessSosEnabledContext. Off, this mount is
              byte-identical to before -- SOSButton still shows the "No
              Active Ride / Call 911" prompt whenever currentRide is unset.
              On, a rider with no active ride can send a real alert. This is
              the ONLY RiderSOS mount site in rider-app that passes these two
              props -- the other three (ride-in-progress, driver-arriving,
              driver-arrived) always have a real rideId and are unaffected. */}
          <RiderSOS
            rideId={currentRide?.id}
            onTrigger={triggerEmergency}
            size="small"
            t={t}
            ridelessSosEnabled={ridelessSosEnabled}
            onTriggerRideless={triggerRidelessEmergency}
          />
        </View>
      </View>

      {isTablet ? (
        <View style={styles.sidePanel}>
          <BottomSheetContent
            colors={colors}
            styles={styles}
            aiMode={aiMode}
            showPromo={showPromo}
            setShowPromo={setShowPromo}
            handleSearchPress={handleSearchPress}
            handleAiPress={handleAiPress}
            handleQuickAction={handleQuickAction}
          />
        </View>
      ) : (
        <BottomSheet
          ref={bottomSheetRef}
          index={0}
          onChange={handleSheetChange}
          snapPoints={snapPoints}
          backgroundStyle={styles.bottomSheetBg}
          handleIndicatorStyle={styles.sheetHandle}
          enablePanDownToClose={false}
          enableDynamicSizing={false}
          enableOverDrag={false}
          overDragResistanceFactor={20}
        >
          <BottomSheetScrollView
            showsVerticalScrollIndicator={false}
            keyboardShouldPersistTaps="handled"
            bounces={false}
            overScrollMode="never"
            contentContainerStyle={styles.bottomSheetContent}
          >
            <BottomSheetContent
              colors={colors}
              styles={styles}
              aiMode={aiMode}
              showPromo={showPromo}
              setShowPromo={setShowPromo}
              handleSearchPress={handleSearchPress}
              handleAiPress={handleAiPress}
              handleQuickAction={handleQuickAction}
            />
          </BottomSheetScrollView>
        </BottomSheet>
      )}
    </View>
  );
}

function BottomSheetContent({
  colors, styles, aiMode, showPromo, setShowPromo, handleSearchPress, handleAiPress, handleQuickAction,
}: {
  colors: ThemeColors; styles: any;
  aiMode: 'enabled' | 'coming_soon' | 'hidden';
  showPromo: boolean; setShowPromo: (v: boolean) => void;
  handleSearchPress: () => void; handleAiPress: () => void; handleQuickAction: (type: string) => void;
}) {
  // Rotate through PROMO_MESSAGES while the banner is visible and there's more
  // than one message. Pauses (and resets) when the rider dismisses the banner.
  const [promoIndex, setPromoIndex] = useState(0);
  useEffect(() => {
    if (!showPromo || PROMO_MESSAGES.length <= 1) return;
    const id = setInterval(() => {
      setPromoIndex((i) => (i + 1) % PROMO_MESSAGES.length);
    }, PROMO_ROTATE_MS);
    return () => clearInterval(id);
  }, [showPromo]);
  const promo = PROMO_MESSAGES[promoIndex] ?? PROMO_MESSAGES[0];

  return (
    <>
      <View style={styles.searchRow}>
        <TouchableOpacity
          style={styles.searchBar}
          onPress={handleSearchPress}
          accessibilityLabel="Where to? Search for a destination"
          accessibilityRole="button"
        >
          <Ionicons name="search" size={22} color={colors.primary} />
          <Text style={styles.searchPlaceholder}>Where to?</Text>
        </TouchableOpacity>
        {aiMode !== 'hidden' && (
          <TouchableOpacity
            style={styles.aiButton}
            onPress={handleAiPress}
            activeOpacity={0.8}
            accessibilityLabel="AI assistant"
            accessibilityRole="button"
          >
            <View style={styles.aiIconGlow} />
            <Ionicons name="sparkles" size={20} color="#FFFFFF" />
            <Text style={styles.aiButtonText}>AI</Text>
          </TouchableOpacity>
        )}
      </View>

      <View style={styles.quickActions}>
        <TouchableOpacity
          style={styles.quickAction}
          onPress={() => handleQuickAction('home')}
          accessibilityRole="button"
          accessibilityLabel="Go home"
        >
          <View style={styles.quickActionIcon}>
            <Ionicons name="home" size={22} color={colors.primary} />
          </View>
          <Text style={styles.quickActionText}>Home</Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={styles.quickAction}
          onPress={() => handleQuickAction('work')}
          accessibilityRole="button"
          accessibilityLabel="Go to work"
        >
          <View style={styles.quickActionIcon}>
            <Ionicons name="briefcase" size={22} color={colors.primary} />
          </View>
          <Text style={styles.quickActionText}>Work</Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={styles.quickAction}
          onPress={() => handleQuickAction('saved')}
          accessibilityRole="button"
          accessibilityLabel="Saved places"
        >
          <View style={styles.quickActionIcon}>
            <Ionicons name="star" size={22} color={colors.primary} />
          </View>
          <Text style={styles.quickActionText}>Saved</Text>
        </TouchableOpacity>
      </View>

      {showPromo && (
        <View style={styles.promoBanner}>
          <View style={styles.promoIconContainer}>
            <Ionicons name={promo.icon} size={20} color={colors.primary} />
          </View>
          <View style={styles.promoContent}>
            <Text style={styles.promoTitle}>{promo.title}</Text>
            <Text style={styles.promoText}>{promo.text}</Text>
            {PROMO_MESSAGES.length > 1 && (
              <View style={styles.promoDots}>
                {PROMO_MESSAGES.map((_, i) => (
                  <View
                    key={i}
                    style={[styles.promoDot, i === promoIndex && styles.promoDotActive]}
                  />
                ))}
              </View>
            )}
          </View>
          <TouchableOpacity
            onPress={() => setShowPromo(false)}
            style={styles.promoClose}
            accessibilityRole="button"
            accessibilityLabel="Dismiss promotion banner"
            hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }}
          >
            <Ionicons name="close" size={20} color={colors.textDim} />
          </TouchableOpacity>
        </View>
      )}
    </>
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
      position: 'relative',
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.1,
      shadowRadius: 4,
      elevation: 3,
    },
    notifBadge: {
      position: 'absolute', top: 6, right: 6,
      minWidth: 16, height: 16, borderRadius: 8,
      backgroundColor: '#EF4444',
      justifyContent: 'center', alignItems: 'center',
      paddingHorizontal: 3,
    },
    notifBadgeText: { color: '#FFF', fontSize: 10, fontWeight: '700', lineHeight: 14 },
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
    mapControlsWrap: {
      position: 'absolute',
      right: 20,
      bottom: '47%',
      zIndex: 5,
      alignItems: 'center',
      gap: 10,
    },
    mapControls: {
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
    locationButton: {
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
    sosButton: {
      position: 'absolute',
      left: 20,
      bottom: '47%',
      zIndex: 5,
    },
    bottomSheetBg: {
      backgroundColor: colors.surface,
      borderTopLeftRadius: 24,
      borderTopRightRadius: 24,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: -4 },
      shadowOpacity: 0.1,
      shadowRadius: 12,
      elevation: 8,
    },
    bottomSheetContent: {
      paddingHorizontal: 20,
      paddingBottom: 20,
    },
    sheetHandle: {
      width: 40,
      height: 4,
      backgroundColor: colors.border,
      borderRadius: 2,
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
    promoDots: {
      flexDirection: 'row',
      gap: 5,
      marginTop: 8,
    },
    promoDot: {
      width: 6,
      height: 6,
      borderRadius: 3,
      backgroundColor: colors.border,
    },
    promoDotActive: {
      backgroundColor: colors.primary,
      width: 14,
    },
    promoClose: {
      padding: 4,
    },
    sidePanel: {
      width: 340,
      backgroundColor: colors.surface,
      borderLeftWidth: 1,
      borderLeftColor: colors.border,
      shadowColor: '#000',
      shadowOffset: { width: -4, height: 0 },
      shadowOpacity: 0.1,
      shadowRadius: 12,
      elevation: 8,
      paddingTop: 60,
      paddingHorizontal: 20,
      flexShrink: 0 as const,
    },
    notifBanner: {
      marginHorizontal: 16,
      marginTop: 8,
      marginBottom: 4,
      padding: 12,
      borderRadius: 12,
      backgroundColor: colors.surface,
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.1,
      shadowRadius: 4,
      elevation: 3,
    },
    notifBannerLeft: {
      flexDirection: 'row',
      alignItems: 'center',
      flex: 1,
      marginRight: 8,
    },
    notifBannerIconBg: {
      width: 36,
      height: 36,
      borderRadius: 18,
      backgroundColor: '#FEF3C7',
      alignItems: 'center',
      justifyContent: 'center',
      marginRight: 10,
    },
    notifBannerTextContainer: {
      flex: 1,
    },
    notifBannerTitle: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
    },
    notifBannerSub: {
      fontSize: 12,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
      marginTop: 2,
    },
    notifBannerActions: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8,
    },
    notifEnableBtn: {
      backgroundColor: colors.primary,
      paddingHorizontal: 12,
      paddingVertical: 6,
      borderRadius: 16,
    },
    notifEnableBtnText: {
      color: '#FFFFFF',
      fontSize: 12,
      fontFamily: 'PlusJakartaSans_600SemiBold',
    },
    notifDismissBtn: {
      padding: 4,
    },
  });
}
