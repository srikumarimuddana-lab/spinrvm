import { ExpoConfig, ConfigContext } from 'expo/config';

const APP_NAME = 'Spinr Driver';
const BUNDLE_ID = 'com.spinr.driver';
const SCHEME = 'spinr-driver';

export default ({ config }: ConfigContext): ExpoConfig => ({
    ...config,
    name: APP_NAME,
    slug: 'spinr-driver',
    version: '1.0.0',
    orientation: 'portrait',
    icon: './assets/images/icon.png',
    scheme: SCHEME,
    userInterfaceStyle: 'automatic',
    newArchEnabled: true,
    splash: {
        backgroundColor: '#ee2b2b',
        resizeMode: 'contain',
        image: './assets/images/splash-image.png',
        imageWidth: 200,
    },
    ios: {
        supportsTablet: true,
        bundleIdentifier: BUNDLE_ID,
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
        [
            'expo-splash-screen',
            {
                image: './assets/images/splash-image.png',
                imageWidth: 200,
                resizeMode: 'contain',
                backgroundColor: '#ee2b2b'
            }
        ],
        [
            'expo-notifications',
            {
                icon: './assets/images/icon.png',
                color: '#ee2b2b',
                defaultChannel: 'default',
                sounds: []
            }
        ]
    ],
    experiments: {
        typedRoutes: true
    },
    extra: {
        eas: {
            projectId: "your-project-id"
        }
    }
});
