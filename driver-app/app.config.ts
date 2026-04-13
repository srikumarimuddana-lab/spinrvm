import { ExpoConfig, ConfigContext } from 'expo/config';

const APP_NAME = 'Spinr Driver';
const BUNDLE_ID = 'com.spinr.driver'; // driver-only ID — rider app uses com.spinr.user (no clash)
const SCHEME = 'spinr-driver';

export default ({ config }: ConfigContext): ExpoConfig => ({
    ...config,
    name: APP_NAME,
    slug: 'spinrdriver',
    version: '1.0.0',
    orientation: 'portrait',
    icon: './assets/images/icon.png',
    scheme: SCHEME,
    userInterfaceStyle: 'automatic',
    newArchEnabled: true,
    updates: {
        url: 'https://u.expo.dev/1ed02cf4-97cb-4678-b5a2-0881f89abaa8',
    },
    // Bare workflow (after `expo prebuild`) does not support runtime version
    // policies like { policy: 'appVersion' } — EAS Update requires a literal
    // string. Bump this manually when you ship native changes that break
    // JS-bundle compatibility. Keeping it in sync with `version` above is a
    // reasonable default.
    runtimeVersion: '1.0.0',
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
        },
        associatedDomains: [
            'applinks:spinr.app',
        ],
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
        },
        intentFilters: [
            {
                action: 'VIEW',
                autoVerify: true,
                data: [
                    { scheme: 'https', host: 'spinr.app', pathPrefix: '/driver' },
                    { scheme: 'https', host: 'spinr.app', pathPrefix: '/join' },
                ],
                category: ['BROWSABLE', 'DEFAULT'],
            },
        ],
    },
    web: {
        bundler: 'metro',
        output: 'single',
        favicon: './assets/images/favicon.png'
    },
    plugins: [
        'expo-router',
        ['@stripe/stripe-react-native', {
            merchantIdentifier: 'merchant.com.spinr.driver',
            enableGooglePay: true,
        }],
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
        typedRoutes: true
    },
    extra: {
        eas: {
            projectId: "1ed02cf4-97cb-4678-b5a2-0881f89abaa8"
        },
        EXPO_PUBLIC_BACKEND_URL: process.env.EXPO_PUBLIC_BACKEND_URL,
        backendUrl: process.env.EXPO_PUBLIC_BACKEND_URL,
    }
});
