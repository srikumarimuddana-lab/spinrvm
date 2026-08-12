import React, { useState, useEffect, useRef, useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  TextInput,
  FlatList,
  Keyboard,
  ActivityIndicator,
  KeyboardAvoidingView,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import * as Location from 'expo-location';
import { useRideStore } from '../store/rideStore';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { showToast } from '../store/toastStore';
import api from '@shared/api/client';
import { usePlacesAutocomplete } from '@shared/hooks/usePlacesAutocomplete';
import type { PlacePrediction } from '@shared/api/places';

export default function SearchDestinationScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ mapPickField?: string; mapPickLat?: string; mapPickLng?: string; mapPickAddress?: string }>();
  const {
    pickup, dropoff, stops,
    setPickup, setDropoff, addStop, removeStop, updateStop,
    savedAddresses, fetchSavedAddresses,
    recentSearches, addRecentSearch, loadRecentSearches,
    userLocation, setUserLocation,
    clearEstimates,
  } = useRideStore();

  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const [activeField, setActiveField] = useState<'pickup' | 'dropoff' | number>('dropoff');
  const [pickupText, setPickupText] = useState(pickup?.address || 'Current Location');
  const [dropoffText, setDropoffText] = useState(dropoff?.address || '');
  const [stopTexts, setStopTexts] = useState<string[]>(stops.map(s => s.address || ''));
  const [searchQuery, setSearchQuery] = useState('');

  // Bias prediction results to the rider's location so e.g. "Walmart"
  // surfaces nearby stores first instead of generic matches across Canada.
  // Fall back to pickup coordinates (often set from GPS on the home screen)
  // when userLocation hasn't propagated to the store yet.
  const biasLat = userLocation?.latitude ?? (pickup?.lat || null);
  const biasLng = userLocation?.longitude ?? (pickup?.lng || null);
  const bias = biasLat != null && biasLng != null && (biasLat !== 0 || biasLng !== 0)
    ? { lat: biasLat, lng: biasLng, radiusMeters: 50000 }
    : null;

  // Gate: hold the search query until we have a location OR 2 seconds pass.
  // Without this, if GPS hasn't resolved yet the very first request fires with
  // no location= param and Google returns Canada-wide results (Ontario Walmart
  // instead of Regina Walmart). The 2s timeout is a graceful fallback for
  // users who have denied location permission.
  const [locationReady, setLocationReady] = useState(!!bias);
  useEffect(() => {
    // locationReady isn't a dep of this effect (only bias's lat/lng are)
    // and is guarded, so once true this can't re-fire itself.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (bias && !locationReady) setLocationReady(true);
  }, [bias?.lat, bias?.lng]);
  useEffect(() => {
    if (locationReady) return;
    const t = setTimeout(() => setLocationReady(true), 2000);
    return () => clearTimeout(t);
  }, []);

  const {
    predictions,
    loading: isSearching,
    clear: clearPredictions,
    rotateSessionToken,
    sessionToken,
  } = usePlacesAutocomplete(locationReady ? searchQuery : '', bias);

  const pickupRef = useRef<TextInput>(null);
  const dropoffRef = useRef<TextInput>(null);
  const stopRefs = useRef<(TextInput | null)[]>([]);

  // Handle return from map picker
  useEffect(() => {
    if (params.mapPickLat && params.mapPickLng && params.mapPickAddress) {
      const location = {
        address: params.mapPickAddress,
        lat: parseFloat(params.mapPickLat),
        lng: parseFloat(params.mapPickLng),
      };
      addRecentSearch(location);
      if (params.mapPickField === 'pickup') {
        setPickup(location);
        // Deps are the map-pick lat/lng params; pickupText isn't a dep, so
        // this can't retrigger this effect.
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setPickupText(location.address);
        if (!dropoff) setActiveField('dropoff');
      } else {
        setDropoff(location);
        setDropoffText(location.address);
      }
    }
  }, [params.mapPickLat, params.mapPickLng]);

  useEffect(() => {
    fetchSavedAddresses();
    loadRecentSearches();
    // If home screen didn't get GPS yet, fetch it now.
    // IMPORTANT: only set userLocation when GPS actually succeeds. Faking it
    // with DEFAULT_LATITUDE/LONGITUDE (Saskatoon) silently mis-biases place
    // search for riders in other cities — they'd see Saskatoon Walmarts when
    // searching in Regina, for example.
    if (!userLocation) {
      (async () => {
        let { status } = await Location.getForegroundPermissionsAsync();
        if (status !== 'granted') {
          const res = await Location.requestForegroundPermissionsAsync();
          status = res.status;
        }
        if (status !== 'granted') return;
        try {
          const loc = await Location.getCurrentPositionAsync({
            accuracy: Location.Accuracy.Balanced,
          });
          setUserLocation({
            latitude: loc.coords.latitude,
            longitude: loc.coords.longitude,
          });
        } catch (e) {
          // Don't fall back to a hardcoded city — that biases place
          // search (e.g. "Walmart") to the wrong region. Leave
          // userLocation null so autocomplete sends no location bias.
          console.warn('Could not get current location:', e);
        }
      })();
    }
  }, []);

  // When GPS location is available (from store), set pickup immediately
  useEffect(() => {
    if (userLocation) {
      if (!pickup || pickup.address === 'Current Location') {
        setPickup({ address: 'Current Location', lat: userLocation.latitude, lng: userLocation.longitude });
        // pickupText isn't a dep of this effect (only userLocation is), and
        // the outer guard prevents this from firing once a real pickup is set.
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setPickupText('Current Location');
      }
    }
  }, [userLocation]);

  // Sync stop texts when stops change
  useEffect(() => {
    // stopTexts isn't a dep of this effect (only stops.length is), so no loop.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setStopTexts(stops.map(s => s.address || ''));
  }, [stops.length]);

  // Focus the active field
  useEffect(() => {
    const t = setTimeout(() => {
      if (activeField === 'pickup') pickupRef.current?.focus();
      else if (activeField === 'dropoff') dropoffRef.current?.focus();
      else if (typeof activeField === 'number') stopRefs.current[activeField]?.focus();
    }, 100);
    return () => clearTimeout(t);
  }, [activeField]);

  const getPlaceDetails = async (placeId: string): Promise<{ lat: number; lng: number; address: string } | null> => {
    try {
      const params = new URLSearchParams({
        place_id: placeId,
        session_token: sessionToken,
      });
      const { data } = await api.get<{ lat: number | null; lng: number | null; formatted_address: string }>(
        `/maps/places/details?${params.toString()}`,
      );
      // Selection closes the billing session — start a fresh one for the next field.
      rotateSessionToken();
      if (data && data.lat != null && data.lng != null) {
        return {
          lat: data.lat,
          lng: data.lng,
          address: data.formatted_address,
        };
      }
    } catch (error) {
      console.log('Place details error:', error);
    }
    return null;
  };

  const handleSelectPrediction = async (prediction: PlacePrediction) => {
    Keyboard.dismiss();
    clearPredictions();
    setSearchQuery('');
    const details = await getPlaceDetails(prediction.place_id);
    if (!details) return;

    // Address AND coordinates both from the details response — one source, one
    // moment in time. (The old code paired the autocomplete `description` with
    // the details coords: usually consistent, but two responses from two Google
    // products, and the description lacks the postal code so recents dedupe
    // could never match a saved-place copy of the same address.) place_id rides
    // along so a later tap on this recents entry can re-resolve fresh
    // coordinates instead of trusting stored ones.
    const location = {
      address: details.address || prediction.description,
      lat: details.lat,
      lng: details.lng,
      place_id: prediction.place_id,
    };

    // Save to recent searches
    addRecentSearch(location);

    if (activeField === 'pickup') {
      setPickup(location);
      setPickupText(location.address);
      if (stops.length > 0 && !stops[0].address) {
        setActiveField(0);
      } else if (!dropoff) {
        setActiveField('dropoff');
      }
    } else if (activeField === 'dropoff') {
      setDropoff(location);
      setDropoffText(location.address);
      // Don't auto-navigate; user clicks "Search Ride" button
      if (!pickup) {
        setActiveField('pickup');
      }
    } else if (typeof activeField === 'number') {
      updateStop(activeField, location);
      const newTexts = [...stopTexts];
      newTexts[activeField] = location.address;
      setStopTexts(newTexts);
      if (activeField < stops.length - 1) {
        setActiveField(activeField + 1);
      } else if (!dropoff) {
        setActiveField('dropoff');
      }
    }
  };

  const handleTextChange = (text: string, field: 'pickup' | 'dropoff' | number) => {
    // Editing the text ORPHANS the previously selected point: the store keeps
    // address+coords as a pair, and a pair whose address the rider just typed
    // over is no longer what they mean. Leaving it in place kept the Search
    // button enabled and booked the OLD pin under the NEW text (incident: bar
    // read "4500 Gordon Rd" while the dropoff pin sat kilometres away on a
    // previously selected point). Clearing re-disables Search until the rider
    // picks a suggestion, which re-binds address and coords atomically.
    if (field === 'pickup') {
      setPickupText(text);
      if (pickup && text !== pickup.address) setPickup(null);
    } else if (field === 'dropoff') {
      setDropoffText(text);
      if (dropoff && text !== dropoff.address) setDropoff(null);
    } else {
      const newTexts = [...stopTexts];
      newTexts[field] = text;
      setStopTexts(newTexts);
      const stop = stops[field];
      if (stop && stop.address && text !== stop.address) {
        updateStop(field, { address: '', lat: 0, lng: 0 });
      }
    }
    setActiveField(field);
    setSearchQuery(text);
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
      setSearchQuery(text);
    } else {
      setSearchQuery('');
      clearPredictions();
    }
  };

  const handleSelectLocation = async (stored: { address: string; lat: number; lng: number; place_id?: string }) => {
    // A stored pair (recent or saved place) is a snapshot from an arbitrary
    // point in the past — replaying its coordinates verbatim is how one bad
    // write once became a permanent wrong pin (address said Gordon Rd, pin sat
    // on Glide Crescent, forever). When the entry carries a place_id,
    // re-resolve fresh coordinates from Google; fall back to the stored pair
    // only when the lookup fails or the entry predates place_id tracking.
    let location = stored;
    if (stored.place_id) {
      const details = await getPlaceDetails(stored.place_id);
      if (details) {
        location = {
          address: details.address || stored.address,
          lat: details.lat,
          lng: details.lng,
          place_id: stored.place_id,
        };
      }
    }
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
    setSearchQuery('');
    clearPredictions();
  };

  const handleSearchRide = () => {
    if (pickup && dropoff) {
      // Force a fresh quote for the current pickup/dropoff/stops: drop any
      // estimate, route line, or promo left over from a previous search so
      // ride-options recomputes from scratch rather than flashing stale data.
      clearEstimates();
      router.push('/confirm-pickup');
    }
  };

  const canSearchRide = !!(pickup && dropoff && pickup.address && dropoff.address);

  const renderPrediction = ({ item }: { item: PlacePrediction }) => (
    <TouchableOpacity
      style={styles.predictionRow}
      onPress={() => handleSelectPrediction(item)}
      accessibilityRole="button"
      accessibilityLabel={item.structured_formatting?.main_text || item.description}
      accessibilityHint={item.structured_formatting?.secondary_text ? `${item.structured_formatting.secondary_text} — double-tap to select` : 'Double-tap to select this location'}
    >
      <View style={styles.predictionIcon}>
        <Ionicons name="location-outline" size={20} color={colors.textDim} />
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
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior="padding"
      >
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity
          style={styles.backButton}
          onPress={() => router.back()}
          accessibilityRole="button"
          accessibilityLabel="Go back"
          hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
        >
          <Ionicons name="arrow-back" size={24} color={colors.text} />
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
              placeholderTextColor={colors.textDim}
              selectTextOnFocus
              returnKeyType="next"
              onSubmitEditing={() => {
                if (stops.length > 0) stopRefs.current[0]?.focus();
                else dropoffRef.current?.focus();
              }}
            />
            {pickupText ? (
              <TouchableOpacity
                onPress={() => {
                  setPickupText('');
                  setPickup(null as any);
                  setActiveField('pickup');
                  pickupRef.current?.focus();
                }}
                accessibilityRole="button"
                accessibilityLabel="Clear pickup location"
                hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }}
              >
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
                  placeholderTextColor={colors.textDim}
                  returnKeyType="next"
                  onSubmitEditing={() => {
                    if (index < stops.length - 1) stopRefs.current[index + 1]?.focus();
                    else dropoffRef.current?.focus();
                  }}
                />
              </View>
              <TouchableOpacity
                onPress={() => handleRemoveStop(index)}
                style={styles.removeButton}
                accessibilityRole="button"
                accessibilityLabel={`Remove stop ${index + 1}`}
                hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
              >
                <Ionicons name="close-circle" size={20} color={colors.textDim} />
              </TouchableOpacity>
            </View>
            <View style={styles.connectorLine} />
          </View>
        ))}

        {/* Dropoff */}
        <View style={styles.inputRow}>
          <View style={[styles.dot, { backgroundColor: colors.primary }]} />
          <View style={[styles.inputWrapper, activeField === 'dropoff' && styles.inputActive]}>
            <TextInput
              ref={dropoffRef}
              style={styles.textInput}
              value={dropoffText}
              onChangeText={(text) => handleTextChange(text, 'dropoff')}
              onFocus={() => handleFieldFocus('dropoff')}
              placeholder="Where to?"
              placeholderTextColor={colors.textDim}
              autoFocus
              returnKeyType="search"
              onSubmitEditing={handleSearchRide}
            />
            {dropoffText ? (
              <TouchableOpacity
                onPress={() => {
                  setDropoffText('');
                  setDropoff(null as any);
                  setActiveField('dropoff');
                  dropoffRef.current?.focus();
                }}
                accessibilityRole="button"
                accessibilityLabel="Clear destination"
                hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }}
              >
                <Ionicons name="close-circle" size={18} color="#CCC" />
              </TouchableOpacity>
            ) : null}
          </View>
        </View>

        {/* Add Stop Button */}
        {stops.length < 3 && (
          <TouchableOpacity
            style={styles.addStopButton}
            onPress={handleAddStop}
            accessibilityRole="button"
            accessibilityLabel="Add a stop"
            accessibilityHint="Adds an intermediate stop to your route"
          >
            <Ionicons name="add-circle" size={20} color={colors.primary} />
            <Text style={styles.addStopText}>Add stop</Text>
          </TouchableOpacity>
        )}
      </View>

      {/* Predictions / Suggestions */}
      <View style={styles.suggestionsContainer}>
        {isSearching && (
          <View style={styles.loadingRow}>
            <ActivityIndicator size="small" color={colors.primary} />
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
                  {/* Quick Access: Current Location, Set on Map */}
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
                        setSearchQuery('');
                        clearPredictions();
                      }}
                      accessibilityRole="button"
                      accessibilityLabel="Use current location as pickup"
                      accessibilityHint="Sets your GPS position as the pickup point"
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

                  {/* Set Location on Map */}
                  <TouchableOpacity
                    style={styles.predictionRow}
                    onPress={() => {
                      router.push({ pathname: '/pick-on-map', params: { field: activeField === 'pickup' ? 'pickup' : 'dropoff' } } as any);
                    }}
                    accessibilityRole="button"
                    accessibilityLabel="Set location on map"
                    accessibilityHint="Opens the map to choose a precise location by tapping"
                  >
                    <View style={[styles.predictionIcon, { backgroundColor: '#EDE9FE' }]}>
                      <Ionicons name="map" size={20} color="#7C3AED" />
                    </View>
                    <View style={styles.predictionContent}>
                      <Text style={styles.predictionMainText}>Set location on map</Text>
                      <Text style={styles.predictionSecondaryText}>Choose a point on the map</Text>
                    </View>
                  </TouchableOpacity>

                  {/* Home & Work Quick Buttons */}
                  <View style={styles.quickChips}>
                    {(() => {
                      const homeAddr = savedAddresses.find(a => a.name?.toLowerCase() === 'home');
                      return (
                        <TouchableOpacity
                          style={styles.quickChip}
                          onPress={() => {
                            if (homeAddr) {
                              handleSelectLocation({ address: homeAddr.address, lat: homeAddr.lat, lng: homeAddr.lng });
                            } else {
                              showToast('No Home Address', 'Set your home address in Account > Saved Places', 'info');
                            }
                          }}
                          accessibilityRole="button"
                          accessibilityLabel={homeAddr ? `Home — ${homeAddr.address}` : 'Home — not set'}
                          accessibilityHint={homeAddr ? 'Double-tap to use your home address' : 'Set your home address in Account, Saved Places'}
                        >
                          <Ionicons name="home" size={18} color={homeAddr ? colors.primary : '#BBB'} />
                          <Text style={[styles.quickChipText, !homeAddr && { color: '#BBB' }]}>Home</Text>
                          {homeAddr && <Ionicons name="chevron-forward" size={14} color="#CCC" />}
                        </TouchableOpacity>
                      );
                    })()}
                    {(() => {
                      const workAddr = savedAddresses.find(a => a.name?.toLowerCase() === 'work');
                      return (
                        <TouchableOpacity
                          style={styles.quickChip}
                          onPress={() => {
                            if (workAddr) {
                              handleSelectLocation({ address: workAddr.address, lat: workAddr.lat, lng: workAddr.lng });
                            } else {
                              showToast('No Work Address', 'Set your work address in Account > Saved Places', 'info');
                            }
                          }}
                          accessibilityRole="button"
                          accessibilityLabel={workAddr ? `Work — ${workAddr.address}` : 'Work — not set'}
                          accessibilityHint={workAddr ? 'Double-tap to use your work address' : 'Set your work address in Account, Saved Places'}
                        >
                          <Ionicons name="briefcase" size={18} color={workAddr ? '#3B82F6' : '#BBB'} />
                          <Text style={[styles.quickChipText, !workAddr && { color: '#BBB' }]}>Work</Text>
                          {workAddr && <Ionicons name="chevron-forward" size={14} color="#CCC" />}
                        </TouchableOpacity>
                      );
                    })()}
                  </View>

                  {/* Favourites */}
                  {savedAddresses.filter(a => a.name?.toLowerCase() !== 'home' && a.name?.toLowerCase() !== 'work').length > 0 && (
                    <View>
                      <Text style={styles.sectionTitle}>Favourites</Text>
                      {savedAddresses
                        .filter(a => a.name?.toLowerCase() !== 'home' && a.name?.toLowerCase() !== 'work')
                        .map((addr, index) => (
                        <TouchableOpacity
                          key={`fav-${index}`}
                          style={styles.predictionRow}
                          onPress={() => {
                            handleSelectLocation({ address: addr.address, lat: addr.lat, lng: addr.lng });
                          }}
                          accessibilityRole="button"
                          accessibilityLabel={`${addr.name || 'Saved'} — ${addr.address}`}
                          accessibilityHint="Double-tap to select this saved place"
                        >
                          <View style={[styles.predictionIcon, { backgroundColor: '#FFF7ED' }]}>
                            <Ionicons name="star" size={20} color="#F59E0B" />
                          </View>
                          <View style={styles.predictionContent}>
                            <Text style={styles.predictionMainText}>{addr.name || 'Saved'}</Text>
                            <Text style={styles.predictionSecondaryText} numberOfLines={1}>{addr.address}</Text>
                          </View>
                        </TouchableOpacity>
                      ))}
                    </View>
                  )}

                  {/* Home & Work (if set, show as list items too) */}
                  {savedAddresses.filter(a => a.name?.toLowerCase() === 'home' || a.name?.toLowerCase() === 'work').length > 0 && (
                    <View>
                      <Text style={styles.sectionTitle}>Saved Places</Text>
                      {savedAddresses
                        .filter(a => a.name?.toLowerCase() === 'home' || a.name?.toLowerCase() === 'work')
                        .map((addr, index) => (
                        <TouchableOpacity
                          key={`saved-${index}`}
                          style={styles.predictionRow}
                          onPress={() => {
                            handleSelectLocation({ address: addr.address, lat: addr.lat, lng: addr.lng });
                          }}
                          accessibilityRole="button"
                          accessibilityLabel={`${addr.name} — ${addr.address}`}
                          accessibilityHint="Double-tap to select this saved place"
                        >
                          <View style={[styles.predictionIcon, {
                            backgroundColor: addr.name?.toLowerCase() === 'home' ? '#FEE2E2' : '#DBEAFE',
                          }]}>
                            <Ionicons
                              name={addr.name?.toLowerCase() === 'home' ? 'home' : 'briefcase'}
                              size={20}
                              color={addr.name?.toLowerCase() === 'home' ? colors.primary : '#3B82F6'}
                            />
                          </View>
                          <View style={styles.predictionContent}>
                            <Text style={styles.predictionMainText}>{addr.name}</Text>
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
                            // Pass the WHOLE entry — rebuilding it here once
                            // stripped place_id, which silently disabled the
                            // re-resolve and replayed stored coordinates.
                            handleSelectLocation(search);
                          }}
                          accessibilityRole="button"
                          accessibilityLabel={search.address}
                          accessibilityHint="Double-tap to use this recent location"
                        >
                          <View style={[styles.predictionIcon, { backgroundColor: colors.surfaceLight }]}>
                            <Ionicons name="time-outline" size={20} color={colors.textDim} />
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
          accessibilityRole="button"
          accessibilityLabel="Search for rides"
          accessibilityHint={canSearchRide ? 'Shows available vehicles for your route' : 'Set both a pickup and destination to continue'}
          accessibilityState={{ disabled: !canSearchRide }}
        >
          <Ionicons name="search" size={20} color="#FFF" />
          <Text style={styles.searchRideButtonText}>Search Ride</Text>
        </TouchableOpacity>
      </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
      flex: 1,
      backgroundColor: colors.surface,
    },
    header: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: 16,
      paddingVertical: 12,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
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
      color: colors.text,
    },
    inputsContainer: {
      paddingHorizontal: 20,
      paddingVertical: 16,
      backgroundColor: colors.surfaceLight,
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
      backgroundColor: colors.border,
      marginLeft: 5,
      marginVertical: 2,
    },
    inputWrapper: {
      flex: 1,
      flexDirection: 'row',
      alignItems: 'center',
      paddingHorizontal: 16,
      backgroundColor: colors.surface,
      borderRadius: 12,
      borderWidth: 1.5,
      borderColor: 'transparent',
    },
    inputActive: {
      borderColor: colors.primary,
    },
    textInput: {
      flex: 1,
      height: 46,
      fontSize: 15,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.text,
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
      color: colors.primary,
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
      color: colors.textDim,
    },
    sectionTitle: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.textDim,
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
      borderBottomColor: colors.surfaceLight,
    },
    predictionIcon: {
      width: 40,
      height: 40,
      borderRadius: 20,
      backgroundColor: colors.surfaceLight,
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
      color: colors.text,
    },
    predictionSecondaryText: {
      fontSize: 13,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
      marginTop: 2,
    },
    quickChips: {
      flexDirection: 'row',
      gap: 10,
      marginTop: 14,
      marginBottom: 6,
    },
    quickChip: {
      flex: 1,
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: colors.surfaceLight,
      borderRadius: 14,
      paddingVertical: 14,
      paddingHorizontal: 14,
      gap: 8,
    },
    quickChipText: {
      flex: 1,
      fontSize: 15,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
    },
    searchButtonContainer: {
      paddingHorizontal: 20,
      paddingVertical: 12,
      borderTopWidth: 1,
      borderTopColor: colors.border,
      backgroundColor: colors.surface,
    },
    searchRideButton: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: colors.primary,
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
}
