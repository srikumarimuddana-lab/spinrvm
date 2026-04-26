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
        image: './assets/images/icon.png',
        imageWidth: 160,
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
            'applinks:spinr-track.app',
        ],
        // Required by Apple for any app using required-reason APIs (enforced from May 2024).
        // Missing this causes App Store / TestFlight rejection at upload time (ITMS-91053).
        privacyManifests: {
            NSPrivacyTracking: false,
            NSPrivacyAccessedAPITypes: [
                {
                    NSPrivacyAccessedAPIType: 'NSPrivacyAccessedAPICategoryFileTimestamp',
                    NSPrivacyAccessedAPITypeReasons: ['C617.1', '0A2A.1'],
                },
                {
                    NSPrivacyAccessedAPIType: 'NSPrivacyAccessedAPICategoryUserDefaults',
                    NSPrivacyAccessedAPITypeReasons: ['CA92.1'],
                },
                {
                    NSPrivacyAccessedAPIType: 'NSPrivacyAccessedAPICategorySystemBootTime',
                    NSPrivacyAccessedAPITypeReasons: ['35F9.1'],
                },
                {
                    NSPrivacyAccessedAPIType: 'NSPrivacyAccessedAPICategoryDiskSpace',
                    NSPrivacyAccessedAPITypeReasons: ['E174.1'],
                },
            ],
            NSPrivacyCollectedDataTypes: [
                { NSPrivacyCollectedDataType: 'NSPrivacyCollectedDataTypePreciseLocation', NSPrivacyCollectedDataTypeLinked: true, NSPrivacyCollectedDataTypeTracking: false, NSPrivacyCollectedDataTypePurposes: ['NSPrivacyCollectedDataTypePurposeAppFunctionality'] },
                { NSPrivacyCollectedDataType: 'NSPrivacyCollectedDataTypeCoarseLocation', NSPrivacyCollectedDataTypeLinked: false, NSPrivacyCollectedDataTypeTracking: false, NSPrivacyCollectedDataTypePurposes: ['NSPrivacyCollectedDataTypePurposeAppFunctionality'] },
                { NSPrivacyCollectedDataType: 'NSPrivacyCollectedDataTypeName', NSPrivacyCollectedDataTypeLinked: true, NSPrivacyCollectedDataTypeTracking: false, NSPrivacyCollectedDataTypePurposes: ['NSPrivacyCollectedDataTypePurposeAppFunctionality'] },
                { NSPrivacyCollectedDataType: 'NSPrivacyCollectedDataTypePhoneNumber', NSPrivacyCollectedDataTypeLinked: true, NSPrivacyCollectedDataTypeTracking: false, NSPrivacyCollectedDataTypePurposes: ['NSPrivacyCollectedDataTypePurposeAppFunctionality'] },
                { NSPrivacyCollectedDataType: 'NSPrivacyCollectedDataTypeEmailAddress', NSPrivacyCollectedDataTypeLinked: true, NSPrivacyCollectedDataTypeTracking: false, NSPrivacyCollectedDataTypePurposes: ['NSPrivacyCollectedDataTypePurposeAppFunctionality'] },
                { NSPrivacyCollectedDataType: 'NSPrivacyCollectedDataTypePaymentInfo', NSPrivacyCollectedDataTypeLinked: true, NSPrivacyCollectedDataTypeTracking: false, NSPrivacyCollectedDataTypePurposes: ['NSPrivacyCollectedDataTypePurposeAppFunctionality'] },
                { NSPrivacyCollectedDataType: 'NSPrivacyCollectedDataTypeDeviceID', NSPrivacyCollectedDataTypeLinked: true, NSPrivacyCollectedDataTypeTracking: false, NSPrivacyCollectedDataTypePurposes: ['NSPrivacyCollectedDataTypePurposeAppFunctionality'] },
                { NSPrivacyCollectedDataType: 'NSPrivacyCollectedDataTypeCrashData', NSPrivacyCollectedDataTypeLinked: false, NSPrivacyCollectedDataTypeTracking: false, NSPrivacyCollectedDataTypePurposes: ['NSPrivacyCollectedDataTypePurposeAnalytics'] },
                { NSPrivacyCollectedDataType: 'NSPrivacyCollectedDataTypePerformanceData', NSPrivacyCollectedDataTypeLinked: false, NSPrivacyCollectedDataTypeTracking: false, NSPrivacyCollectedDataTypePurposes: ['NSPrivacyCollectedDataTypePurposeAnalytics'] },
            ],
        },
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
                    { scheme: 'https', host: 'spinr.app', pathPrefix: '/ride' },
                    { scheme: 'https', host: 'spinr.app', pathPrefix: '/promo' },
                    { scheme: 'https', host: 'spinr.app', pathPrefix: '/join' },
                    { scheme: 'https', host: 'spinr-track.app', pathPrefix: '/' },
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
            merchantIdentifier: 'merchant.com.spinr.user',
            enableGooglePay: true,
        }],
        'expo-font',
        [
            'expo-splash-screen',
            {
                image: './assets/images/icon.png',
                imageWidth: 160,
                resizeMode: 'contain',
                backgroundColor: '#ee2b2b'
            }
        ],
        '@react-native-firebase/app',
        '@react-native-firebase/messaging',
        '@react-native-firebase/crashlytics',
        '@react-native-firebase/app-check',
        // LogRocket native module needs Android minSdk 25.
        ['expo-build-properties', { android: { minSdkVersion: 25 } }],
        '@logrocket/react-native',
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
