import { useState, useEffect, useRef, useCallback } from 'react';
import * as Location from 'expo-location';
import { Dimensions } from 'react-native';
import api from '@shared/api/client';
import { API_URL } from '@shared/config';

const { height } = Dimensions.get('window');

interface UseLocationOptions {
    isOnline: boolean;
    userId?: string;
}

interface LocationData {
    latitude: number;
    longitude: number;
    timestamp: number;
}

export const useLocation = ({ isOnline, userId }: UseLocationOptions) => {
    const [location, setLocation] = useState<Location.LocationObject | null>(null);
    const locationSubRef = useRef<Location.LocationSubscription | null>(null);
    const locationBufferRef = useRef<LocationData[]>([]);

    // Request location permissions
    const requestPermissions = useCallback(async () => {
        const { status } = await Location.requestForegroundPermissionsAsync();
        return status === 'granted';
    }, []);

    // Get current location
    const getCurrentLocation = useCallback(async () => {
        try {
            const loc = await Location.getCurrentPositionAsync({
                accuracy: Location.Accuracy.High,
            });
            setLocation(loc);
            return loc;
        } catch (error) {
            console.log('Error getting current location:', error);
            return null;
        }
    }, []);

    // Start location tracking
    const startTracking = useCallback(async () => {
        const hasPermission = await requestPermissions();
        if (!hasPermission) return;

        try {
            // Get initial location
            const loc = await getCurrentLocation();
            if (loc) setLocation(loc);

            // Subscribe to location updates
            locationSubRef.current = await Location.watchPositionAsync(
                {
                    accuracy: Location.Accuracy.High,
                    distanceInterval: 10,
                    timeInterval: 5000,
                },
                (loc) => {
                    setLocation(loc);

                    // Add to buffer for batch upload
                    locationBufferRef.current.push({
                        latitude: loc.coords.latitude,
                        longitude: loc.coords.longitude,
                        timestamp: loc.timestamp,
                    });
                }
            );
        } catch (error) {
            console.log('Error starting location tracking:', error);
        }
    }, [requestPermissions, getCurrentLocation]);

    // Stop location tracking
    const stopTracking = useCallback(() => {
        if (locationSubRef.current) {
            locationSubRef.current.remove();
            locationSubRef.current = null;
        }
    }, []);

    // Upload location batch to server
    const uploadLocationBatch = useCallback(async () => {
        if (!isOnline || !userId || locationBufferRef.current.length === 0) return;

        const batch = [...locationBufferRef.current];
        locationBufferRef.current = [];

        try {
            await api.post('/drivers/location-batch', {
                driver_id: userId,
                locations: batch,
            });
        } catch (error) {
            console.log('Error uploading location batch:', error);
            // Re-add to buffer on failure
            locationBufferRef.current = [...batch, ...locationBufferRef.current];
        }
    }, [isOnline, userId]);

    // Set up location tracking when online
    useEffect(() => {
        if (isOnline) {
            startTracking();
        } else {
            stopTracking();
        }

        return () => {
            stopTracking();
        };
    }, [isOnline, startTracking, stopTracking]);

    // Upload location batch periodically
    useEffect(() => {
        if (!isOnline) return;

        const interval = setInterval(() => {
            uploadLocationBatch();
        }, 10000); // Upload every 10 seconds

        return () => clearInterval(interval);
    }, [isOnline, uploadLocationBatch]);

    return {
        location,
        getCurrentLocation,
        startTracking,
        stopTracking,
        uploadLocationBatch,
    };
};

export default useLocation;
