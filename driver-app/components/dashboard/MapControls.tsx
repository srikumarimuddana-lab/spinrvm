import React from 'react';
import { View, StyleSheet, TouchableOpacity } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import MapView from 'react-native-maps';
import SpinrConfig from '@shared/config/spinr.config';

const COLORS = {
  overlay: 'rgba(255, 255, 255, 0.95)',
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
    <>
      {/* Zoom Controls */}
      <View style={styles.zoomControls}>
        <TouchableOpacity style={styles.zoomBtn} onPress={handleZoomIn}>
          <Ionicons name="add" size={22} color={COLORS.text} />
        </TouchableOpacity>
        <View style={styles.zoomDivider} />
        <TouchableOpacity style={styles.zoomBtn} onPress={handleZoomOut}>
          <Ionicons name="remove" size={22} color={COLORS.text} />
        </TouchableOpacity>
      </View>

      {/* My Location Button */}
      <TouchableOpacity style={styles.myLocationBtn} onPress={handleRecenter}>
        <Ionicons name="locate" size={22} color={COLORS.accent} />
      </TouchableOpacity>
    </>
  );
};

const styles = StyleSheet.create({
  zoomControls: {
    position: 'absolute',
    right: 16,
    bottom: 380,
    borderRadius: 14,
    backgroundColor: COLORS.overlay,
    elevation: 4,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.25,
    shadowRadius: 4,
    overflow: 'hidden',
  },
  zoomBtn: {
    width: 44,
    height: 44,
    justifyContent: 'center',
    alignItems: 'center',
  },
  zoomDivider: {
    height: 1,
    backgroundColor: COLORS.border,
    marginHorizontal: 8,
  },
  myLocationBtn: {
    position: 'absolute',
    right: 16,
    bottom: 320,
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: COLORS.overlay,
    justifyContent: 'center',
    alignItems: 'center',
    elevation: 4,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.3,
    shadowRadius: 4,
  },
});

export default MapControls;
