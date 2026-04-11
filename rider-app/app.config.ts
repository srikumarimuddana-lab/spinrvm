import { ExpoConfig, ConfigContext } from 'expo/config';

const APP_NAME = 'Spinr';
const BUNDLE_ID = 'com.spinr.user'; // rider-only ID — driver app uses com.spinr.driver (no clash)
const SCHEME = 'spinr-user';

export default ({ config }: ConfigContext): ExpoConfig => ({
    ...config,
    name: APP_NAME,
    slug: 'spinr-rider',
    version: '1.0.0',
    orientation: 'portrait',
    icon: './assets/images/icon.png',
    scheme: SCHEME,
    userInterfaceStyle: 'automatic',
    newArchEnabled: true,
    updates: {
        url: 'https://u.expo.dev/8f1e4f60-720e-46b0-9b71-33c13d3af043',
    },
    runtimeVersion: {
        policy: 'appVersion',
    },
    splash: {
        backgroundColor: '#ee2b2b',
        resizeMode: 'contain',
        image: './assets/images/splash-image.png',
        imageWidth: 200,
    },
    ios: {
        supportsTablet: true,
        bundleIdentifier: BUNDLE_ID,
        googleServicesFile: './GoogleService-Info.plist',
        config: {
            googleMapsApiKey: process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY
        }
    },
    android: {
        adaptiveIcon: {
            foregroundImage: './assets/images/adaptive-icon.png',
            backgroundColor: '#ee2b2b'
        },
        edgeToEdgeEnabled: true,
        package: BUNDLE_ID,
        googleServicesFile: './google-services.json',
        config: {
            googleMaps: {
                apiKey: process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY
            }
        }
    },
    web: {
        bundler: 'metro',
        output: 'single',
        favicon: './assets/images/favicon.png'
    },
    plugins: [
        'expo-router',
        ['@stripe/stripe-react-native', {
            merchantIdentifier: 'merchant.com.spinr.user',
            enableGooglePay: true,
        }],
        'expo-font',
        [
            'expo-splash-screen',
            {
                image: './assets/images/splash-image.png',
                imageWidth: 200,
                resizeMode: 'contain',
                backgroundColor: '#ee2b2b'
            }
        ],
        '@react-native-firebase/app',
        '@react-native-firebase/messaging',
        '@react-native-firebase/crashlytics',
        '@react-native-firebase/app-check',
    ],
    experiments: {
        typedRoutes: false
    },
    extra: {
        eas: {
            projectId: "8f1e4f60-720e-46b0-9b71-33c13d3af043"
        },
        EXPO_PUBLIC_BACKEND_URL: process.env.EXPO_PUBLIC_BACKEND_URL,
        backendUrl: process.env.EXPO_PUBLIC_BACKEND_URL,
        googleMapsApiKey: process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY
    }
});
