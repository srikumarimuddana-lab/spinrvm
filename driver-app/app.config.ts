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
    // @ts-expect-error newArchEnabled is valid Expo config but not yet typed in ExpoConfig
    newArchEnabled: false, // disabled: pre-launch stability over perf; re-enable post go-live as a planned migration
    updates: {
        url: 'https://u.expo.dev/1ed02cf4-97cb-4678-b5a2-0881f89abaa8',
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
    ios: ({
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
        ],
        // Required by Apple for any app using required-reason APIs (enforced from May 2024).
        // Missing this causes App Store / TestFlight rejection at upload time.
        privacyManifests: {
            NSPrivacyTracking: false,
            NSPrivacyAccessedAPITypes: [
                // File timestamps — Crashlytics reads .crash file timestamps
                {
                    NSPrivacyAccessedAPIType: 'NSPrivacyAccessedAPICategoryFileTimestamp',
                    NSPrivacyAccessedAPITypeReasons: [
                        'C617.1', // third-party crash reporter (Firebase Crashlytics)
                        '0A2A.1', // timestamps of files the app itself created
                    ],
                },
                // NSUserDefaults — expo-secure-store, React Native bridge, Firebase SDK
                {
                    NSPrivacyAccessedAPIType: 'NSPrivacyAccessedAPICategoryUserDefaults',
                    NSPrivacyAccessedAPITypeReasons: [
                        'CA92.1', // first-party app reading its own defaults
                    ],
                },
                // System boot time — Firebase Crashlytics, LogRocket uptime metrics
                {
                    NSPrivacyAccessedAPIType: 'NSPrivacyAccessedAPICategorySystemBootTime',
                    NSPrivacyAccessedAPITypeReasons: [
                        '35F9.1', // crash reporter / diagnostic tool
                    ],
                },
                // Disk space — writing app-specific cached files; Crashlytics log rotation
                {
                    NSPrivacyAccessedAPIType: 'NSPrivacyAccessedAPICategoryDiskSpace',
                    NSPrivacyAccessedAPITypeReasons: [
                        'E174.1', // write files to disk for app functionality
                    ],
                },
            ],
            NSPrivacyCollectedDataTypes: [
                // Precise GPS location — required for ride dispatch and navigation
                {
                    NSPrivacyCollectedDataType: 'NSPrivacyCollectedDataTypePreciseLocation',
                    NSPrivacyCollectedDataTypeLinked: true,
                    NSPrivacyCollectedDataTypeTracking: false,
                    NSPrivacyCollectedDataTypePurposes: ['NSPrivacyCollectedDataTypePurposeAppFunctionality'],
                },
                // Name — driver profile display
                {
                    NSPrivacyCollectedDataType: 'NSPrivacyCollectedDataTypeName',
                    NSPrivacyCollectedDataTypeLinked: true,
                    NSPrivacyCollectedDataTypeTracking: false,
                    NSPrivacyCollectedDataTypePurposes: ['NSPrivacyCollectedDataTypePurposeAppFunctionality'],
                },
                // Phone number — OTP authentication
                {
                    NSPrivacyCollectedDataType: 'NSPrivacyCollectedDataTypePhoneNumber',
                    NSPrivacyCollectedDataTypeLinked: true,
                    NSPrivacyCollectedDataTypeTracking: false,
                    NSPrivacyCollectedDataTypePurposes: ['NSPrivacyCollectedDataTypePurposeAppFunctionality'],
                },
                // Email — driver account / receipts
                {
                    NSPrivacyCollectedDataType: 'NSPrivacyCollectedDataTypeEmailAddress',
                    NSPrivacyCollectedDataTypeLinked: true,
                    NSPrivacyCollectedDataTypeTracking: false,
                    NSPrivacyCollectedDataTypePurposes: ['NSPrivacyCollectedDataTypePurposeAppFunctionality'],
                },
                // Payment info — Stripe Connect payout details (no raw card data stored)
                {
                    NSPrivacyCollectedDataType: 'NSPrivacyCollectedDataTypePaymentInfo',
                    NSPrivacyCollectedDataTypeLinked: true,
                    NSPrivacyCollectedDataTypeTracking: false,
                    NSPrivacyCollectedDataTypePurposes: ['NSPrivacyCollectedDataTypePurposeAppFunctionality'],
                },
                // Device ID — Firebase instance ID for push notifications
                {
                    NSPrivacyCollectedDataType: 'NSPrivacyCollectedDataTypeDeviceID',
                    NSPrivacyCollectedDataTypeLinked: true,
                    NSPrivacyCollectedDataTypeTracking: false,
                    NSPrivacyCollectedDataTypePurposes: ['NSPrivacyCollectedDataTypePurposeAppFunctionality'],
                },
                // Crash data — Firebase Crashlytics
                {
                    NSPrivacyCollectedDataType: 'NSPrivacyCollectedDataTypeCrashData',
                    NSPrivacyCollectedDataTypeLinked: false,
                    NSPrivacyCollectedDataTypeTracking: false,
                    NSPrivacyCollectedDataTypePurposes: ['NSPrivacyCollectedDataTypePurposeAnalytics'],
                },
                // Performance data — LogRocket session replay metrics
                {
                    NSPrivacyCollectedDataType: 'NSPrivacyCollectedDataTypePerformanceData',
                    NSPrivacyCollectedDataTypeLinked: false,
                    NSPrivacyCollectedDataTypeTracking: false,
                    NSPrivacyCollectedDataTypePurposes: ['NSPrivacyCollectedDataTypePurposeAnalytics'],
                },
            ],
        },
    } as any),
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
        './plugins/withGradleWrapper',
        'expo-router',
        ['expo-location', {
            locationAlwaysAndWhenInUsePermission:
                'Spinr Driver needs your location to dispatch ride requests, navigate to pickups, and share your live position with riders during active trips.',
            locationWhenInUsePermission:
                'Spinr Driver needs your location to dispatch ride requests and navigate to pickups.',
            locationAlwaysPermission:
                'Spinr Driver needs background location so you keep receiving ride offers and stay visible to riders when the app is in the background.',
            isAndroidBackgroundLocationEnabled: true,
            isIosBackgroundLocationEnabled: true,
        }],
        [
            'expo-splash-screen',
            {
                image: './assets/images/icon.png',
                imageWidth: 160,
                resizeMode: 'contain',
                backgroundColor: '#ee2b2b'
            }
        ],
        'expo-font',
        'expo-secure-store',
        '@react-native-firebase/app',
        '@react-native-firebase/messaging',
        '@react-native-firebase/crashlytics',
        // IMPORTANT: Before enabling App Check enforcement you must manually
        // register each platform in Firebase Console → App Check → Apps:
        //   iOS    : register bundle ID using Apple DeviceCheck
        //   Android: register package name using Play Integrity
        // Add debug tokens for local builds via Firebase Console → App Check
        // → Apps → overflow menu → "Manage debug tokens".
        ['@react-native-firebase/app-check', {
            ios: { appCheckProviderFactory: 'DeviceCheck' },
            android: { appCheckProviderFactory: 'playIntegrity' },
        }],
        // SDK 55 / RN 0.85.2 requires compileSdkVersion 35 + Kotlin 2.1.20 (from @react-native/gradle-plugin libs.versions.toml).
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
