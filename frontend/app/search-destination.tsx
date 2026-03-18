import React, { useState, useEffect, useRef } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { GooglePlacesAutocomplete } from 'react-native-google-places-autocomplete';
import * as Location from 'expo-location';
import { useRideStore } from '../store/rideStore';
import { useAuthStore } from '../store/authStore';
import SpinrConfig from '../config/spinr.config';

const GOOGLE_MAPS_API_KEY = process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY;



export default function SearchDestinationScreen() {
  const router = useRouter();
  const { user } = useAuthStore();
  const {
    pickup, dropoff, stops,
    setPickup, setDropoff, addStop, removeStop, updateStop,
  } = useRideStore();

  const [activeField, setActiveField] = useState<'pickup' | 'dropoff' | number>('dropoff');
  const [pickupText, setPickupText] = useState(pickup?.address || 'Current Location');
  const [dropoffText, setDropoffText] = useState(dropoff?.address || '');
  const [stopTexts, setStopTexts] = useState<string[]>(stops.map(s => s.address || ''));
  const [userLocation, setUserLocation] = useState<any>(null);

  const pickupRef = useRef<any>(null);
  const dropoffRef = useRef<any>(null);
  const stopRefs = useRef<any[]>([]);

  useEffect(() => {
    (async () => {
      const { status } = await Location.getForegroundPermissionsAsync();
      if (status === 'granted') {
        const loc = await Location.getCurrentPositionAsync({});
        setUserLocation({
          latitude: loc.coords.latitude,
          longitude: loc.coords.longitude,
        });
      }
    })();
  }, []);

  useEffect(() => {
    if (!pickup) {
      if (userLocation) {
        setPickup({ address: 'Current Location', lat: userLocation.latitude, lng: userLocation.longitude });
        setPickupText('Current Location');
      } else {
        const defaultPickup = user?.city === 'Regina'
          ? { address: 'Current Location', lat: 50.4452, lng: -104.6189 }
          : { address: 'Current Location', lat: 52.1332, lng: -106.6700 };
        setPickup(defaultPickup);
        setPickupText('Current Location');
      }
    }
  }, [userLocation]);

  // Sync stop texts when stops change
  useEffect(() => {
    setStopTexts(stops.map(s => s.address || ''));
  }, [stops.length]);

  // Focus the active field
  useEffect(() => {
    setTimeout(() => {
      if (activeField === 'pickup') pickupRef.current?.focus();
      else if (activeField === 'dropoff') dropoffRef.current?.focus();
      else if (typeof activeField === 'number') stopRefs.current[activeField]?.focus();
    }, 100);
  }, [activeField]);

  const handleAddStop = () => {
    if (stops.length >= 3) return;
    addStop({ address: '', lat: 0, lng: 0 });
    setActiveField(stops.length);
  };

  const handleRemoveStop = (index: number) => {
    removeStop(index);
    const newTexts = [...stopTexts];
    newTexts.splice(index, 1);
    setStopTexts(newTexts);
  };

  const handleSearchRide = () => {
    if (pickup && dropoff) {
      router.push('/ride-options');
    }
  };

  const canSearchRide = !!(pickup && dropoff && pickup.address && dropoff.address);

  // Focus the correct autocomplete programmatically if needed (optional)
  useEffect(() => {
    if (activeField === 'pickup') {
      pickupRef.current?.focus();
    } else if (activeField === 'dropoff') {
      dropoffRef.current?.focus();
    }
  }, [activeField]);

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity style={styles.backButton} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color="#1A1A1A" />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Set destination</Text>
        <View style={{ width: 44 }} />
      </View>

      {/* Location Inputs */}
      <View style={styles.inputsContainer}>
        {/* Pickup Autocomplete */}
        <View style={styles.inputRow}>
          <View style={[styles.dot, { backgroundColor: '#10B981', marginTop: 15 }]} />
          <View style={styles.inputWrapper}>
            <GooglePlacesAutocomplete
              ref={pickupRef}
              placeholder="Pickup location"
              minLength={2}
              fetchDetails={true}
              onPress={(data, details = null) => {
                if (details) {
                  const location = { address: data.description, lat: details.geometry.location.lat, lng: details.geometry.location.lng };
                  setPickup(location);
                  setActiveField('dropoff');
                }
              }}
              query={{
                key: GOOGLE_MAPS_API_KEY,
                language: 'en',
                components: 'country:ca',
              }}
              styles={{
                textInputContainer: { width: '100%', backgroundColor: 'transparent' },
                textInput: styles.textInput,
                predefinedPlacesDescription: { color: '#1faadb' },
                listView: { position: 'absolute', top: 50, zIndex: 10, elevation: 10, backgroundColor: 'white' }
              }}
              textInputProps={{
                placeholderTextColor: "#999",
                onFocus: () => setActiveField('pickup'),
                defaultValue: pickupText
              }}
            />
          </View>
        </View>

        <View style={styles.connectorLine} />

        {/* Dropoff Autocomplete */}
        <View style={styles.inputRow}>
          <View style={[styles.dot, { backgroundColor: SpinrConfig.theme.colors.primary, marginTop: 15 }]} />
          <View style={styles.inputWrapper}>
            <GooglePlacesAutocomplete
              ref={dropoffRef}
              placeholder="Where to?"
              minLength={2}
              fetchDetails={true}
              onPress={(data, details = null) => {
                if (details) {
                  const location = { address: data.description, lat: details.geometry.location.lat, lng: details.geometry.location.lng };
                  setDropoff(location);
                }
              }}
              query={{
                key: GOOGLE_MAPS_API_KEY,
                language: 'en',
                components: 'country:ca',
              }}
              styles={{
                textInputContainer: { width: '100%', backgroundColor: 'transparent' },
                textInput: styles.textInput,
                predefinedPlacesDescription: { color: '#1faadb' },
                listView: { position: 'absolute', top: 50, zIndex: 10, elevation: 10, backgroundColor: 'white' }
              }}
              textInputProps={{
                placeholderTextColor: "#999",
                onFocus: () => setActiveField('dropoff'),
                autoFocus: true,
                defaultValue: dropoffText
              }}
            />
          </View>
        </View>
      </View>

      {/* Search Ride Button */}
      <View style={styles.searchButtonContainer}>
        <TouchableOpacity
          style={[styles.searchRideButton, !canSearchRide && styles.searchRideButtonDisabled]}
          onPress={handleSearchRide}
          disabled={!canSearchRide}
          activeOpacity={0.8}
        >
          <Ionicons name="search" size={20} color="#FFF" />
          <Text style={styles.searchRideButtonText}>Search Ride</Text>
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#FFFFFF',
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#F0F0F0',
  },
  backButton: {
    width: 44,
    height: 44,
    justifyContent: 'center',
    alignItems: 'center',
  },
  headerTitle: {
    fontSize: 18,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
  },
  inputsContainer: {
    paddingHorizontal: 20,
    paddingVertical: 16,
    backgroundColor: '#F9F9F9',
  },
  inputRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  dot: {
    width: 12,
    height: 12,
    borderRadius: 6,
    marginRight: 16,
  },
  connectorLine: {
    width: 2,
    height: 20,
    backgroundColor: '#E0E0E0',
    marginLeft: 5,
    marginVertical: 2,
  },
  inputWrapper: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    borderWidth: 1.5,
    borderColor: 'transparent',
  },
  inputActive: {
    borderColor: SpinrConfig.theme.colors.primary,
  },
  textInput: {
    flex: 1,
    height: 46,
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#1A1A1A',
  },
  removeButton: {
    padding: 8,
  },
  addStopButton: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 10,
    marginLeft: 28,
  },
  addStopText: {
    marginLeft: 8,
    fontSize: 14,
    color: SpinrConfig.theme.colors.primary,
    fontFamily: 'PlusJakartaSans_600SemiBold',
  },
  searchButtonContainer: {
    paddingHorizontal: 20,
    paddingVertical: 12,
    borderTopWidth: 1,
    borderTopColor: '#F0F0F0',
    backgroundColor: '#FFFFFF',
  },
  searchRideButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: SpinrConfig.theme.colors.primary,
    borderRadius: 28,
    paddingVertical: 16,
    gap: 10,
  },
  searchRideButtonDisabled: {
    backgroundColor: '#CCC',
  },
  searchRideButtonText: {
    fontSize: 17,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#FFFFFF',
  },
});
