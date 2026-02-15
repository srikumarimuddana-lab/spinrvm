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
    if (Platform.OS === 'web' && typeof window !== 'undefined') {
        return 'http://localhost:8000';
    }
    return '';
};

export const API_URL = getBackendUrl();
