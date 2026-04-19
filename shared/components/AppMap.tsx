import React from 'react';
import MapView, { PROVIDER_GOOGLE } from 'react-native-maps';
import { StyleSheet, Platform } from 'react-native';

const GOOGLE_MAPS_API_KEY = process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY || '';

const AppMap = React.forwardRef((props: any, ref: any) => {
    const provider = Platform.OS === 'android'
        ? (GOOGLE_MAPS_API_KEY ? PROVIDER_GOOGLE : undefined)
        : undefined;
    return (
        <MapView
            ref={ref}
            provider={provider}
            {...props}
        />
    );
});

export default AppMap;
