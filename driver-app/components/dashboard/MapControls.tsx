import React, { useMemo } from 'react';
import { View, StyleSheet, TouchableOpacity, Platform } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import MapView from 'react-native-maps';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

const SafeBlurView: React.FC<{ intensity?: number; tint?: string; style?: any; children?: React.ReactNode }> = ({ style, children }) => (
  <View style={style}>{children}</View>
);

type LocationLike = { coords: { latitude: number; longitude: number } };

interface MapControlsProps {
  mapRef: React.RefObject<MapView>;
  location: LocationLike | null;
  currentRegionRef: React.RefObject<{ latitudeDelta: number; longitudeDelta: number }>;
  onRecenter?: () => Promise<LocationLike | null | void> | LocationLike | null | void;
  /** When set, renders a compass toggle for course-up map rotation. */
  courseUpEnabled?: boolean;
  onToggleCourseUp?: () => void;
}

export const MapControls: React.FC<MapControlsProps> = ({
  mapRef,
  location,
  currentRegionRef,
  onRecenter,
  courseUpEnabled,
  onToggleCourseUp,
}) => {
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const insets = useSafeAreaInsets();
  const handleZoomIn = () => {
    if (mapRef.current) {
      mapRef.current.animateToRegion({
        latitude: location?.coords.latitude || 52.1332,
        longitude: location?.coords.longitude || -106.6700,
        latitudeDelta: currentRegionRef.current.latitudeDelta / 2,
        longitudeDelta: currentRegionRef.current.longitudeDelta / 2,
      }, 300);
    }
  };

  const handleZoomOut = () => {
    if (mapRef.current) {
      mapRef.current.animateToRegion({
        latitude: location?.coords.latitude || 52.1332,
        longitude: location?.coords.longitude || -106.6700,
        latitudeDelta: Math.min(currentRegionRef.current.latitudeDelta * 2, 90),
        longitudeDelta: Math.min(currentRegionRef.current.longitudeDelta * 2, 90),
      }, 300);
    }
  };

  // Live-testing report 2026-08-30: recenter "does not work that responsive".
  // The previous version awaited onRecenter() — a fresh GPS fetch
  // (Location.getCurrentPositionAsync, up to a 15s timeout) — before
  // animating the camera AT ALL, so every tap visibly did nothing for
  // however long that fetch took. But the app already tracks a live,
  // continuously-updating position via watchPositionAsync — `location` is
  // rarely more than a few seconds stale. Snap to it INSTANTLY, then let
  // onRecenter's fresh fetch run in the background purely to correct the
  // rare stale/cold-start case; the ordinary follow-camera effect (course-up
  // rotation) picks up that correction on its own once it lands, so a
  // second manual animate isn't needed here.
  const handleRecenter = () => {
    if (location && mapRef.current) {
      mapRef.current.animateToRegion({
        latitude: location.coords.latitude,
        longitude: location.coords.longitude,
        latitudeDelta: 0.01,
        longitudeDelta: 0.01,
      }, 400);
    }
    // Fire-and-forget: resets followRef + refreshes the fix in the
    // background (useDriverDashboard.refreshLocation). Swallow rejection —
    // a failed background refresh isn't user-facing when the instant snap
    // above already ran.
    void Promise.resolve(onRecenter?.()).catch(() => {});
  };

  return (
    <View style={[styles.controlsContainer, { bottom: insets.bottom + 120 }]}>
      {/* Zoom Controls */}
      <View style={styles.shadowWrapper}>
        <SafeBlurView intensity={Platform.OS === 'ios' ? 40 : 100} tint="light" style={styles.blurContainer}>
          <TouchableOpacity style={styles.zoomBtn} onPress={handleZoomIn} activeOpacity={0.7} accessibilityRole="button" accessibilityLabel="Zoom in">
            <Ionicons name="add" size={24} color={colors.text} />
          </TouchableOpacity>
          <View style={styles.zoomDivider} />
          <TouchableOpacity style={styles.zoomBtn} onPress={handleZoomOut} activeOpacity={0.7} accessibilityRole="button" accessibilityLabel="Zoom out">
            <Ionicons name="remove" size={24} color={colors.text} />
          </TouchableOpacity>
        </SafeBlurView>
      </View>

      {/* Course-up / North-up toggle */}
      {onToggleCourseUp && (
        <View style={[styles.shadowWrapper, { marginTop: 12 }]}>
          <SafeBlurView intensity={Platform.OS === 'ios' ? 40 : 100} tint="light" style={[styles.blurContainer, styles.myLocationBtn]}>
            <TouchableOpacity
              style={styles.btnInner}
              onPress={onToggleCourseUp}
              activeOpacity={0.7}
              accessibilityRole="button"
              accessibilityState={{ selected: !!courseUpEnabled }}
              accessibilityLabel={courseUpEnabled ? 'Switch map to north-up' : 'Switch map to course-up (rotate with travel direction)'}
            >
              <Ionicons
                name={courseUpEnabled ? 'navigate' : 'compass-outline'}
                size={24}
                color={courseUpEnabled ? colors.primary : colors.text}
              />
            </TouchableOpacity>
          </SafeBlurView>
        </View>
      )}

      {/* My Location Button */}
      <View style={[styles.shadowWrapper, { marginTop: 12 }]}>
        <SafeBlurView intensity={Platform.OS === 'ios' ? 40 : 100} tint="light" style={[styles.blurContainer, styles.myLocationBtn]}>
          <TouchableOpacity style={styles.btnInner} onPress={handleRecenter} activeOpacity={0.7} accessibilityRole="button" accessibilityLabel="Center map on my location">
            <Ionicons name="locate" size={24} color={colors.primary} />
          </TouchableOpacity>
        </SafeBlurView>
      </View>
    </View>
  );
};

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    controlsContainer: {
      position: 'absolute',
      right: 16,
      bottom: 120,
      alignItems: 'flex-end',
    },
    shadowWrapper: {
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 4 },
      shadowOpacity: 0.15,
      shadowRadius: 10,
      elevation: 6,
    },
    blurContainer: {
      borderRadius: 16,
      overflow: 'hidden',
      backgroundColor: colors.overlay,
      borderWidth: 1,
      borderColor: colors.border,
    },
    zoomBtn: {
      width: 48,
      height: 48,
      justifyContent: 'center',
      alignItems: 'center',
    },
    btnInner: {
      width: '100%',
      height: '100%',
      justifyContent: 'center',
      alignItems: 'center',
    },
    zoomDivider: {
      height: 1,
      backgroundColor: colors.border,
      marginHorizontal: 10,
    },
    myLocationBtn: {
      width: 48,
      height: 48,
      borderRadius: 24,
    },
  });
}

export default MapControls;
