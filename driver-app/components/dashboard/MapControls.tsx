import React from 'react';
import { View, StyleSheet, TouchableOpacity, Platform } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import MapView from 'react-native-maps';
import { BlurView } from 'expo-blur';
import SpinrConfig from '@shared/config/spinr.config';

const COLORS = {
  overlay: 'rgba(255, 255, 255, 0.8)',
  text: SpinrConfig.theme.colors.text,
  border: SpinrConfig.theme.colors.border,
  accent: SpinrConfig.theme.colors.primary,
};

interface MapControlsProps {
  mapRef: React.RefObject<MapView>;
  location: { coords: { latitude: number; longitude: number } } | null;
  currentRegionRef: React.RefObject<{ latitudeDelta: number; longitudeDelta: number }>;
}

export const MapControls: React.FC<MapControlsProps> = ({
  mapRef,
  location,
  currentRegionRef,
}) => {
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

  const handleRecenter = () => {
    if (location && mapRef.current) {
      mapRef.current.animateToRegion({
        latitude: location.coords.latitude,
        longitude: location.coords.longitude,
        latitudeDelta: 0.01,
        longitudeDelta: 0.01,
      }, 500);
    }
  };

  return (
    <View style={styles.controlsContainer}>
      {/* Zoom Controls */}
      <View style={styles.shadowWrapper}>
        <BlurView intensity={Platform.OS === 'ios' ? 40 : 100} tint="light" style={styles.blurContainer}>
          <TouchableOpacity style={styles.zoomBtn} onPress={handleZoomIn} activeOpacity={0.7}>
            <Ionicons name="add" size={24} color={COLORS.text} />
          </TouchableOpacity>
          <View style={styles.zoomDivider} />
          <TouchableOpacity style={styles.zoomBtn} onPress={handleZoomOut} activeOpacity={0.7}>
            <Ionicons name="remove" size={24} color={COLORS.text} />
          </TouchableOpacity>
        </BlurView>
      </View>

      {/* My Location Button */}
      <View style={[styles.shadowWrapper, { marginTop: 12 }]}>
        <BlurView intensity={Platform.OS === 'ios' ? 40 : 100} tint="light" style={[styles.blurContainer, styles.myLocationBtn]}>
          <TouchableOpacity style={styles.btnInner} onPress={handleRecenter} activeOpacity={0.7}>
            <Ionicons name="locate" size={24} color={COLORS.accent} />
          </TouchableOpacity>
        </BlurView>
      </View>
    </View>
  );
};

const styles = StyleSheet.create({
  controlsContainer: {
    position: 'absolute',
    right: 16,
    bottom: 160, // Relocated lower since idle panel is now transparent HUD
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
    backgroundColor: COLORS.overlay,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.7)',
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
    backgroundColor: 'rgba(0,0,0,0.06)',
    marginHorizontal: 10,
  },
  myLocationBtn: {
    width: 48,
    height: 48,
    borderRadius: 24,
  },
});

export default MapControls;
