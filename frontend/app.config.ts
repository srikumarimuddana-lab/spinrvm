import { ExpoConfig, ConfigContext } from 'expo/config';

const IS_DRIVER = process.env.APP_VARIANT === 'driver';
const APP_NAME = IS_DRIVER ? 'Spinr Driver' : 'Spinr';
const BUNDLE_ID = IS_DRIVER ? 'com.spinr.driver' : 'com.spinr.user';
const SCHEME = IS_DRIVER ? 'spinr-driver' : 'spinr-user';

export default ({ config }: ConfigContext): ExpoConfig => ({
    ...config,
    name: APP_NAME,
    slug: 'spinr',
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
        output: 'static',
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
        ]
    ],
    experiments: {
        typedRoutes: true
    },
    extra: {
        APP_VARIANT: process.env.APP_VARIANT,
        eas: {
            projectId: "your-project-id" // EAS will auto-fill or you can set it
        }
    }
});
