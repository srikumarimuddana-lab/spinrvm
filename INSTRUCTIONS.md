# Google Maps & Places API Integration Instructions

Currently, the frontend uses a "Map Placeholder" and mock location data. To achieve a "bug-free perfect design", you must integrate real mapping and geocoding capabilities.

## Prerequisites

1.  **Google Maps API Key**: You need an API key with the following APIs enabled:
    *   Maps SDK for Android
    *   Maps SDK for iOS
    *   Places API
    *   Directions API (for routing)
2.  **Configuration**: Add your API key to `app.json` (or `app.config.js`) and `.env` file (prefixed with `EXPO_PUBLIC_`).

## Step 1: Install Dependencies

You need to install `react-native-maps` and `react-native-google-places-autocomplete`.

```bash
npm install react-native-maps react-native-google-places-autocomplete
# or
yarn add react-native-maps react-native-google-places-autocomplete
```

## Step 2: Configure App (app.json)

Update `app.json` to include the API key for Android and iOS.

```json
{
  "expo": {
    "android": {
      "config": {
        "googleMaps": {
          "apiKey": "YOUR_ANDROID_API_KEY"
        }
      }
    },
    "ios": {
      "config": {
        "googleMapsApiKey": "YOUR_IOS_API_KEY"
      }
    }
  }
}
```

## Step 3: Implement Map Component

Replace the `View` placeholder in `frontend/app/(tabs)/index.tsx` and `frontend/app/ride-options.tsx` with `MapView`.

**Example `components/Map.tsx`:**

```tsx
import React from 'react';
import MapView, { Marker, PROVIDER_GOOGLE } from 'react-native-maps';
import { StyleSheet, View } from 'react-native';

interface MapProps {
  pickup?: { lat: number; lng: number };
  dropoff?: { lat: number; lng: number };
  drivers?: Array<{ id: string; lat: number; lng: number }>;
}

export default function Map({ pickup, dropoff, drivers }: MapProps) {
  return (
    <View style={styles.container}>
      <MapView
        provider={PROVIDER_GOOGLE}
        style={styles.map}
        initialRegion={{
          latitude: pickup?.lat || 52.1332,
          longitude: pickup?.lng || -106.6700,
          latitudeDelta: 0.0922,
          longitudeDelta: 0.0421,
        }}
      >
        {pickup && <Marker coordinate={{ latitude: pickup.lat, longitude: pickup.lng }} title="Pickup" pinColor="green" />}
        {dropoff && <Marker coordinate={{ latitude: dropoff.lat, longitude: dropoff.lng }} title="Dropoff" pinColor="red" />}
        {drivers?.map(driver => (
           <Marker
             key={driver.id}
             coordinate={{ latitude: driver.lat, longitude: driver.lng }}
             image={require('../assets/car-icon.png')} // Ensure you have an asset
           />
        ))}
      </MapView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  map: { width: '100%', height: '100%' },
});
```

## Step 4: Implement Autocomplete

Replace the manual input and mock list in `frontend/app/search-destination.tsx` with `GooglePlacesAutocomplete`.

**Example Usage:**

```tsx
import { GooglePlacesAutocomplete } from 'react-native-google-places-autocomplete';

// ... inside component
<GooglePlacesAutocomplete
  placeholder='Search'
  onPress={(data, details = null) => {
    // 'details' is provided when fetchDetails = true
    const lat = details?.geometry.location.lat;
    const lng = details?.geometry.location.lng;
    const address = data.description;
    // Call setPickup or setDropoff store actions
  }}
  query={{
    key: process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY,
    language: 'en',
  }}
  fetchDetails={true}
  styles={{
    textInput: {
      height: 44,
      borderRadius: 5,
      paddingVertical: 5,
      paddingHorizontal: 10,
      fontSize: 15,
      flex: 1,
    },
  }}
/>
```

## Step 5: Directions (Routing)

To show the route line on the map in `RideOptionsScreen`, use `react-native-maps-directions`.

```bash
npm install react-native-maps-directions
```

Import `MapViewDirections` and use it inside `MapView`.

```tsx
import MapViewDirections from 'react-native-maps-directions';

// ... inside MapView
{pickup && dropoff && (
  <MapViewDirections
    origin={{ latitude: pickup.lat, longitude: pickup.lng }}
    destination={{ latitude: dropoff.lat, longitude: dropoff.lng }}
    apikey={process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY}
    strokeWidth={3}
    strokeColor="hotpink"
  />
)}
```

## Step 6: Backend Verification

Ensure the backend `FareConfig` and `ServiceArea` match the real coordinates you are using. The backend uses PostGIS for `find_nearby_drivers` and `get_service_area_for_point`. Ensure your Supabase database has the `postgis` extension enabled and the RPC functions defined.
