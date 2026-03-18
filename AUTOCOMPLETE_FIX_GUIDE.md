# Fix for SearchDestinationScreen Autocomplete Implementation

Based on my review, the `SearchDestinationScreen` component still contains a hybrid implementation that mixes custom TextInput with manual Google Places API calls alongside the `GooglePlacesAutocomplete` components. This needs to be simplified to use ONLY the `GooglePlacesAutocomplete` components as intended.

## Issues to Fix

1. **Remove custom search logic**: Delete the `searchPlaces`, `getPlaceDetails`, and related state management
2. **Remove manual TextInput implementations**: Replace the custom TextInput wrappers with pure GooglePlacesAutocomplete components
3. **Simplify state management**: Rely more on the ride store and less on local state for text values
4. **Remove custom prediction display**: Eliminate the FlatList that shows manual predictions

## Key Changes Needed

### Remove These Imports:
```typescript
// REMOVE THESE (lines 7-11)
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  TextInput,          // ← REMOVE
  FlatList,           // ← REMOVE
  Keyboard,
  ActivityIndicator,
} from 'react-native';

// REMOVE THIS INTERFACE (lines 23-30)
interface PlacePrediction {
  place_id: string;
  description: string;
  structured_formatting?: {
    main_text: string;
    secondary_text: string;
  };
}
```

### Remove These State Variables:
```typescript
// REMOVE THESE (lines 46-48)
const [predictions, setPredictions] = useState<PlacePrediction[]>([]);
const [isSearching, setIsSearching] = useState(false);
// KEEP userLocation state as it's used for initial location
```

### Remove These Functions:
- `searchPlaces` function (lines 99-124)
- `getPlaceDetails` function (lines 127-143)
- `handleSelectPrediction` function (lines 146-186)
- `handleTextChange` function (lines 189-200)
- `handleFieldFocus` function (lines 215-225)
- `handleSelectLocation` function (lines 228-243)

### Remove These Refs:
```typescript
// REMOVE THESE (lines 50-52)
const pickupRef = useRef<any>(null);
const dropoffRef = useRef<any>(null);
const stopRefs = useRef<(TextInput | null)[]>([]);
const searchTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
```

### Replace Input Sections with Pure GooglePlacesAutocomplete:

#### FOR PICKUP (replace lines 276-310):
```tsx
{/* Pickup Autocomplete */}
<View style={styles.inputRow}>
  <View style={[styles.dot, { backgroundColor: '#10B981', marginTop: 15 }]} />
  <View style={styles.inputWrapper}>
    <GooglePlacesAutocomplete
      placeholder="Pickup location"
      minLength={2}
      fetchDetails={true}
      onPress={(data, details = null) => {
        if (details) {
          const location = { 
            address: data.description, 
            lat: details.geometry.location.lat, 
            lng: details.geometry.location.lng 
          };
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
```

#### FOR DROPOFF (replace lines 314-348):
```tsx
{/* Dropoff Autocomplete */}
<View style={styles.inputRow}>
  <View style={[styles.dot, { backgroundColor: SpinrConfig.theme.colors.primary, marginTop: 15 }]} />
  <View style={styles.inputWrapper}>
    <GooglePlacesAutocomplete
      placeholder="Where to?"
      minLength={2}
      fetchDetails={true}
      onPress={(data, details = null) => {
        if (details) {
          const location = { 
            address: data.description, 
            lat: details.geometry.location.lat, 
            lng: details.geometry.location.lng 
          };
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
```

### Remove These UI Sections:
- Remove the entire "Predictions / Suggestions" section (lines 376-512) that contains the FlatList and custom prediction rendering
- Remove the searchTimeout useEffect (lines 255-261)

### Keep These Essential Parts:
- Location permission and initial location logic (lines 55-67)
- Default pickup location logic (lines 70-82)
- Stop texts synchronization (lines 85-87)
- Active field focus logic (lines 90-96)
- Stop management functions (handleAddStop, handleRemoveStop) - KEEP THESE
- Search ride button logic (handleSearchRide, canSearchRide) - KEEP THESE
- All styling that's still used by the GooglePlacesAutocomplete components

## Benefits of This Approach
1. **Less code**: Removes ~150 lines of custom search logic
2. **More reliable**: Uses the established library's built-in debouncing, caching, and error handling
3. **Consistent UI/UX**: Matches the look and feel of other apps using Google Places Autocomplete
4. **Better maintenance**: Reduces surface area for bugs
5. **Proper integration**: Follows the library's intended usage pattern

## Verification
After making these changes, verify that:
1. No custom Google Places API fetch logic remains (no fetch calls to places.googleapis.com)
2. Only GooglePlacesAutocomplete components are used for text input
3. Location selection properly updates the ride store via setPickup/setDropoff
4. Stop management still works correctly
5. The search ride button properly enables/disables based on pickup/dropoff presence