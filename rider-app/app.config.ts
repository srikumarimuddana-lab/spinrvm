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
    newArchEnabled: false, // disabled: pre-launch stability over perf; re-enable post go-live as a planned migration
    updates: {
        url: 'https://u.expo.dev/8f1e4f60-720e-46b0-9b71-33c13d3af043',
    },
    // Fingerprint policy: EAS hashes the native source tree on every build
    // and uses that hash as the runtimeVersion. JS bundles only ship to clients
    // whose native binary fingerprint matches — eliminating the manual-bump
    // trap where a forgotten runtimeVersion edit could deliver an OTA update
    // to clients with incompatible native code.
    //
    // Supported in SDK 53+ for the prebuild (CNG) workflow via @expo/fingerprint
    // (already a transitive dep of expo@~55.0.20). Pre-launch phase: changing
    // this from a literal '1.0.0' to a fingerprint hash has zero user impact
    // because no production users exist yet.
    runtimeVersion: { policy: 'fingerprint' },
    splash: {
        backgroundColor: '#ee2b2b',
        resizeMode: 'contain',
        image: './assets/images/icon.png',
        imageWidth: 160,
    },
    ios: {
        supportsTablet: true,
        // @ts-expect-error minimumOsVersion is valid Expo config but not yet in SDK 54 type defs
        minimumOsVersion: '16.0', // SDK 55 minimum; was 13.0 on SDK 54
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
        './plugins/withGradleWrapper',
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
        // SDK 55 / RN 0.85.2 requires compileSdkVersion 35 + Kotlin 2.1.20 (from @react-native/gradle-plugin libs.versions.toml).
        // Stripe 0.63.0 and react-native-reanimated 4.x both floor-check these.
        // LogRocket native module requires minSdkVersion 25.
        ['expo-build-properties', {
            android: {
                minSdkVersion: 25,
                compileSdkVersion: 35,
                targetSdkVersion: 35,
                kotlinVersion: '2.1.20',
            }
        }],
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
