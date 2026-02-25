import Constants from 'expo-constants';
import { Platform } from 'react-native';

const getBackendUrl = () => {
    if (process.env.EXPO_PUBLIC_BACKEND_URL) {
        return process.env.EXPO_PUBLIC_BACKEND_URL;
    }
    if (Constants.expoConfig?.hostUri) {
        const host = Constants.expoConfig.hostUri.split(':')[0];
        return `http://${host}:8000`;
    }
    if (process.env.EXPO_PUBLIC_API_URL) {
        return process.env.EXPO_PUBLIC_API_URL;
    }
    console.warn("No EXPO_PUBLIC_BACKEND_URL or EXPO_PUBLIC_API_URL provided!");
    // Default to production if no local environment is detected
    return 'https://spinr-backend.onrender.com';
};

export const API_URL = getBackendUrl();
