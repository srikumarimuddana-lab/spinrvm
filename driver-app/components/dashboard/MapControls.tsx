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
}

export const MapControls: React.FC<MapControlsProps> = ({
  mapRef,
  location,
  currentRegionRef,
  onRecenter,
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

  const handleRecenter = async () => {
    // Fetch a fresh GPS fix first — just animating to the cached `location`
    // prop would only zoom in on yesterday's position.
    let fresh: LocationLike | null = null;
    try {
      const result = await onRecenter?.();
      if (result && typeof result === 'object' && 'coords' in result) {
        fresh = result as LocationLike;
      }
    } catch {}
    const target = fresh ?? location;
    if (target && mapRef.current) {
      mapRef.current.animateToRegion({
        latitude: target.coords.latitude,
        longitude: target.coords.longitude,
        latitudeDelta: 0.01,
        longitudeDelta: 0.01,
      }, 500);
    }
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
