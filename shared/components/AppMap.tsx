import React from 'react';
import MapView, { PROVIDER_DEFAULT, PROVIDER_GOOGLE } from 'react-native-maps';
import { StyleSheet, Platform } from 'react-native';

// Use Google Maps on Android always, and on iOS if configured/preferred (but defaulting to Apple Maps on Expo Go)
// Actually, let's just use default provider which handles platform specifics well
// Unless we want custom styles, then Google is needed.
// Spinr uses simple map for now.

const AppMap = React.forwardRef((props: any, ref: any) => {
    return (
        <MapView
            ref={ref}
            provider={Platform.OS === 'android' ? PROVIDER_GOOGLE : PROVIDER_DEFAULT}
            {...props}
        />
    );
});

export default AppMap;
