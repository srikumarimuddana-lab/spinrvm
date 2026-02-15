import React, { useState, useEffect, useRef } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  TextInput,
  FlatList,
  Keyboard,
  ActivityIndicator,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import * as Location from 'expo-location';
import { useRideStore } from '../store/rideStore';
import { useAuthStore } from '@shared/store/authStore';
import SpinrConfig from '@shared/config/spinr.config';

const GOOGLE_MAPS_API_KEY = process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY;

interface PlacePrediction {
  place_id: string;
  description: string;
  structured_formatting?: {
    main_text: string;
    secondary_text: string;
  };
}

export default function SearchDestinationScreen() {
  const router = useRouter();
  const { user } = useAuthStore();
  const {
    pickup, dropoff, stops,
    setPickup, setDropoff, addStop, removeStop, updateStop,
    savedAddresses, fetchSavedAddresses,
    recentSearches, addRecentSearch, loadRecentSearches,
  } = useRideStore();

  const [activeField, setActiveField] = useState<'pickup' | 'dropoff' | number>('dropoff');
  const [pickupText, setPickupText] = useState(pickup?.address || 'Current Location');
  const [dropoffText, setDropoffText] = useState(dropoff?.address || '');
  const [stopTexts, setStopTexts] = useState<string[]>(stops.map(s => s.address || ''));
  const [predictions, setPredictions] = useState<PlacePrediction[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [userLocation, setUserLocation] = useState<any>(null);

  const pickupRef = useRef<TextInput>(null);
  const dropoffRef = useRef<TextInput>(null);
  const stopRefs = useRef<(TextInput | null)[]>([]);
  const searchTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    fetchSavedAddresses();
    loadRecentSearches();
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

  const searchPlaces = async (query: string) => {
    if (!query || query.length < 2 || !GOOGLE_MAPS_API_KEY) {
      setPredictions([]);
      return;
    }

    if (searchTimeout.current) clearTimeout(searchTimeout.current);

    searchTimeout.current = setTimeout(async () => {
      setIsSearching(true);
      try {
        const locationBias = userLocation
          ? `&location=${userLocation.latitude},${userLocation.longitude}&radius=50000`
          : '';
        const url = `https://maps.googleapis.com/maps/api/place/autocomplete/json?input=${encodeURIComponent(query)}&key=${GOOGLE_MAPS_API_KEY}&language=en&components=country:ca${locationBias}`;
        const response = await fetch(url);
        const data = await response.json();
        if (data.predictions) {
          setPredictions(data.predictions);
        }
      } catch (error) {
        console.log('Places search error:', error);
      } finally {
        setIsSearching(false);
      }
    }, 300);
  };

  const getPlaceDetails = async (placeId: string): Promise<{ lat: number; lng: number; address: string } | null> => {
    if (!GOOGLE_MAPS_API_KEY) return null;
    try {
      const url = `https://maps.googleapis.com/maps/api/place/details/json?place_id=${placeId}&fields=geometry,formatted_address&key=${GOOGLE_MAPS_API_KEY}`;
      const response = await fetch(url);
      const data = await response.json();
      if (data.result) {
        return {
          lat: data.result.geometry.location.lat,
          lng: data.result.geometry.location.lng,
          address: data.result.formatted_address,
        };
      }
    } catch (error) {
      console.log('Place details error:', error);
    }
    return null;
  };

  const handleSelectPrediction = async (prediction: PlacePrediction) => {
    Keyboard.dismiss();
    setPredictions([]);
    const details = await getPlaceDetails(prediction.place_id);
    if (!details) return;

    const location = {
      address: prediction.description,
      lat: details.lat,
      lng: details.lng,
    };

    // Save to recent searches
    addRecentSearch(location);

    if (activeField === 'pickup') {
      setPickup(location);
      setPickupText(prediction.description);
      if (stops.length > 0 && !stops[0].address) {
        setActiveField(0);
      } else if (!dropoff) {
        setActiveField('dropoff');
      }
    } else if (activeField === 'dropoff') {
      setDropoff(location);
      setDropoffText(prediction.description);
      // Don't auto-navigate; user clicks "Search Ride" button
      if (!pickup) {
        setActiveField('pickup');
      }
    } else if (typeof activeField === 'number') {
      updateStop(activeField, location);
      const newTexts = [...stopTexts];
      newTexts[activeField] = prediction.description;
      setStopTexts(newTexts);
      if (activeField < stops.length - 1) {
        setActiveField(activeField + 1);
      } else if (!dropoff) {
        setActiveField('dropoff');
      }
    }
  };

  const handleTextChange = (text: string, field: 'pickup' | 'dropoff' | number) => {
    if (field === 'pickup') {
      setPickupText(text);
    } else if (field === 'dropoff') {
      setDropoffText(text);
    } else {
      const newTexts = [...stopTexts];
      newTexts[field] = text;
      setStopTexts(newTexts);
    }
    setActiveField(field);
    searchPlaces(text);
  };

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

  const handleFieldFocus = (field: 'pickup' | 'dropoff' | number) => {
    setActiveField(field);
    const text = field === 'pickup' ? pickupText
      : field === 'dropoff' ? dropoffText
        : stopTexts[field] || '';
    if (text && text !== 'Current Location') {
      searchPlaces(text);
    } else {
      setPredictions([]);
    }
  };

  const handleSelectLocation = (location: { address: string; lat: number; lng: number }) => {
    addRecentSearch(location);
    if (activeField === 'pickup') {
      setPickup(location);
      setPickupText(location.address);
      if (!dropoff) setActiveField('dropoff');
    } else if (activeField === 'dropoff') {
      setDropoff(location);
      setDropoffText(location.address);
    } else if (typeof activeField === 'number') {
      updateStop(activeField, location);
      const newTexts = [...stopTexts];
      newTexts[activeField] = location.address;
      setStopTexts(newTexts);
    }
    setPredictions([]);
  };

  const handleSearchRide = () => {
    if (pickup && dropoff) {
      router.push('/ride-options');
    }
  };

  const canSearchRide = !!(pickup && dropoff && pickup.address && dropoff.address);

  const renderPrediction = ({ item }: { item: PlacePrediction }) => (
    <TouchableOpacity
      style={styles.predictionRow}
      onPress={() => handleSelectPrediction(item)}
    >
      <View style={styles.predictionIcon}>
        <Ionicons name="location-outline" size={20} color="#666" />
      </View>
      <View style={styles.predictionContent}>
        <Text style={styles.predictionMainText} numberOfLines={1}>
          {item.structured_formatting?.main_text || item.description}
        </Text>
        {item.structured_formatting?.secondary_text && (
          <Text style={styles.predictionSecondaryText} numberOfLines={1}>
            {item.structured_formatting.secondary_text}
          </Text>
        )}
      </View>
    </TouchableOpacity>
  );

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
        {/* Pickup */}
        <View style={styles.inputRow}>
          <View style={[styles.dot, { backgroundColor: '#10B981' }]} />
          <View style={[styles.inputWrapper, activeField === 'pickup' && styles.inputActive]}>
            <TextInput
              ref={pickupRef}
              style={styles.textInput}
              value={pickupText}
              onChangeText={(text) => handleTextChange(text, 'pickup')}
              onFocus={() => handleFieldFocus('pickup')}
              placeholder="Pickup location"
              placeholderTextColor="#999"
              selectTextOnFocus
            />
            {pickupText ? (
              <TouchableOpacity onPress={() => {
                setPickupText('');
                setPickup(null as any);
                setActiveField('pickup');
                pickupRef.current?.focus();
              }}>
                <Ionicons name="close-circle" size={18} color="#CCC" />
              </TouchableOpacity>
            ) : null}
          </View>
        </View>

        <View style={styles.connectorLine} />

        {/* Stops */}
        {stops.map((stop, index) => (
          <View key={index}>
            <View style={styles.inputRow}>
              <View style={[styles.dot, { backgroundColor: '#FFA500' }]} />
              <View style={[styles.inputWrapper, activeField === index && styles.inputActive]}>
                <TextInput
                  ref={(ref) => { stopRefs.current[index] = ref; }}
                  style={styles.textInput}
                  value={stopTexts[index] || ''}
                  onChangeText={(text) => handleTextChange(text, index)}
                  onFocus={() => handleFieldFocus(index)}
                  placeholder="Enter stop address"
                  placeholderTextColor="#999"
                />
              </View>
              <TouchableOpacity onPress={() => handleRemoveStop(index)} style={styles.removeButton}>
                <Ionicons name="close-circle" size={20} color="#999" />
              </TouchableOpacity>
            </View>
            <View style={styles.connectorLine} />
          </View>
        ))}

        {/* Dropoff */}
        <View style={styles.inputRow}>
          <View style={[styles.dot, { backgroundColor: SpinrConfig.theme.colors.primary }]} />
          <View style={[styles.inputWrapper, activeField === 'dropoff' && styles.inputActive]}>
            <TextInput
              ref={dropoffRef}
              style={styles.textInput}
              value={dropoffText}
              onChangeText={(text) => handleTextChange(text, 'dropoff')}
              onFocus={() => handleFieldFocus('dropoff')}
              placeholder="Where to?"
              placeholderTextColor="#999"
              autoFocus
            />
            {dropoffText ? (
              <TouchableOpacity onPress={() => {
                setDropoffText('');
                setDropoff(null as any);
                setActiveField('dropoff');
                dropoffRef.current?.focus();
              }}>
                <Ionicons name="close-circle" size={18} color="#CCC" />
              </TouchableOpacity>
            ) : null}
          </View>
        </View>

        {/* Add Stop Button */}
        {stops.length < 3 && (
          <TouchableOpacity style={styles.addStopButton} onPress={handleAddStop}>
            <Ionicons name="add-circle" size={20} color={SpinrConfig.theme.colors.primary} />
            <Text style={styles.addStopText}>Add stop</Text>
          </TouchableOpacity>
        )}
      </View>

      {/* Predictions / Suggestions */}
      <View style={styles.suggestionsContainer}>
        {isSearching && (
          <View style={styles.loadingRow}>
            <ActivityIndicator size="small" color={SpinrConfig.theme.colors.primary} />
            <Text style={styles.loadingText}>Searching...</Text>
          </View>
        )}

        {predictions.length > 0 ? (
          <FlatList
            data={predictions}
            renderItem={renderPrediction}
            keyExtractor={(item) => item.place_id}
            keyboardShouldPersistTaps="handled"
            style={styles.predictionsList}
          />
        ) : (
          !isSearching && (
            <FlatList
              data={[]}
              renderItem={() => null}
              keyboardShouldPersistTaps="handled"
              ListHeaderComponent={
                <View>
                  {/* Current Location option for pickup */}
                  {activeField === 'pickup' && userLocation && (
                    <TouchableOpacity
                      style={styles.predictionRow}
                      onPress={() => {
                        const location = {
                          address: 'Current Location',
                          lat: userLocation.latitude,
                          lng: userLocation.longitude,
                        };
                        setPickup(location);
                        setPickupText('Current Location');
                        if (!dropoff) setActiveField('dropoff');
                        setPredictions([]);
                      }}
                    >
                      <View style={[styles.predictionIcon, { backgroundColor: '#E8F5E9' }]}>
                        <Ionicons name="navigate" size={20} color="#10B981" />
                      </View>
                      <View style={styles.predictionContent}>
                        <Text style={styles.predictionMainText}>Current Location</Text>
                        <Text style={styles.predictionSecondaryText}>Use your GPS location</Text>
                      </View>
                    </TouchableOpacity>
                  )}

                  {/* Saved Places */}
                  {savedAddresses.length > 0 && (
                    <View>
                      <Text style={styles.sectionTitle}>Saved places</Text>
                      {savedAddresses.map((addr, index) => (
                        <TouchableOpacity
                          key={`saved-${index}`}
                          style={styles.predictionRow}
                          onPress={() => {
                            const location = { address: addr.address, lat: addr.lat, lng: addr.lng };
                            addRecentSearch(location);
                            if (activeField === 'pickup') {
                              setPickup(location);
                              setPickupText(addr.address);
                              setActiveField('dropoff');
                            } else if (activeField === 'dropoff') {
                              setDropoff(location);
                              setDropoffText(addr.address);
                              if (pickup) router.push('/ride-options');
                            } else if (typeof activeField === 'number') {
                              updateStop(activeField, location);
                              const newTexts = [...stopTexts];
                              newTexts[activeField] = addr.address;
                              setStopTexts(newTexts);
                            }
                            setPredictions([]);
                          }}
                        >
                          <View style={styles.predictionIcon}>
                            <Ionicons
                              name={addr.name?.toLowerCase() === 'home' ? 'home' : addr.name?.toLowerCase() === 'work' ? 'briefcase' : 'star'}
                              size={20}
                              color={SpinrConfig.theme.colors.primary}
                            />
                          </View>
                          <View style={styles.predictionContent}>
                            <Text style={styles.predictionMainText}>{addr.name || 'Saved'}</Text>
                            <Text style={styles.predictionSecondaryText} numberOfLines={1}>{addr.address}</Text>
                          </View>
                        </TouchableOpacity>
                      ))}
                    </View>
                  )}

                  {/* Recent Searches */}
                  {recentSearches.length > 0 && (
                    <View>
                      <Text style={styles.sectionTitle}>Recent</Text>
                      {recentSearches.map((search, index) => (
                        <TouchableOpacity
                          key={`recent-${index}`}
                          style={styles.predictionRow}
                          onPress={() => {
                            const location = { address: search.address, lat: search.lat, lng: search.lng };
                            if (activeField === 'pickup') {
                              setPickup(location);
                              setPickupText(search.address);
                              setActiveField('dropoff');
                            } else if (activeField === 'dropoff') {
                              setDropoff(location);
                              setDropoffText(search.address);
                              if (pickup) router.push('/ride-options');
                            } else if (typeof activeField === 'number') {
                              updateStop(activeField, location);
                              const newTexts = [...stopTexts];
                              newTexts[activeField] = search.address;
                              setStopTexts(newTexts);
                            }
                            setPredictions([]);
                          }}
                        >
                          <View style={styles.predictionIcon}>
                            <Ionicons name="time-outline" size={20} color="#999" />
                          </View>
                          <View style={styles.predictionContent}>
                            <Text style={styles.predictionMainText} numberOfLines={1}>{search.address}</Text>
                          </View>
                        </TouchableOpacity>
                      ))}
                    </View>
                  )}
                </View>
              }
            />
          )
        )}
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
  suggestionsContainer: {
    flex: 1,
    paddingHorizontal: 20,
  },
  loadingRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 16,
    gap: 10,
  },
  loadingText: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#999',
  },
  sectionTitle: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#999',
    marginTop: 16,
    marginBottom: 8,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  predictionsList: {
    flex: 1,
  },
  predictionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: '#F5F5F5',
  },
  predictionIcon: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: '#F5F5F5',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 12,
  },
  predictionContent: {
    flex: 1,
  },
  predictionMainText: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#1A1A1A',
  },
  predictionSecondaryText: {
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#999',
    marginTop: 2,
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
