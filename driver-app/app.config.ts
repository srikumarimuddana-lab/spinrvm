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
    newArchEnabled: true, // REQUIRED: react-native-reanimated 4 / react-native-worklets only run on the New Architecture (old arch crashed on first animated screen)
    updates: {
        url: 'https://u.expo.dev/ddcf21cd-edb9-4edd-bd8c-bced1702dd09',
    },
    // Bare workflow requires a literal string runtimeVersion (policies like
    // 'fingerprint'/'appVersion' rejected by EAS CLI). Bump manually when
    // shipping native changes that break JS-bundle compatibility. Pre-launch
    // with no production users, OTA compatibility risk is zero.
    runtimeVersion: '2.0.0', // bumped from 1.0.0: New Architecture is a native/JS-bundle break — old-arch installs must not pull this OTA
    splash: {
        image: './assets/images/splash-mark.png',
        resizeMode: 'contain',
        backgroundColor: '#FFFFFF',
    },
    ios: ({
        supportsTablet: true,
        minimumOsVersion: '16.0', // SDK 55 minimum; was 13.0 on SDK 54 (now in ExpoConfig types)
        bundleIdentifier: BUNDLE_ID,
        googleServicesFile: './GoogleService-Info.plist',
        config: {
            googleMapsApiKey: process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY
        },
        associatedDomains: [
            'applinks:spinr.app',
        ],
        // Purpose strings — Apple rejects uploads with ITMS-90683 if any
        // dependency calls a permission-gated API without a matching string.
        // Driver app needs camera + photo library for onboarding document
        // uploads (license, insurance, vehicle registration) per the
        // Saskatchewan Transportation Act eligibility requirements.
        // Location strings are supplied by the expo-location plugin block.
        infoPlist: {
            NSCameraUsageDescription:
                'Spinr Driver uses your camera to scan and upload your driver license, vehicle insurance, and vehicle registration documents during onboarding and renewal.',
            NSPhotoLibraryUsageDescription:
                'Spinr Driver accesses your photo library so you can upload existing photos of your driver license, vehicle insurance, and vehicle registration.',
        },
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
            backgroundColor: '#FFFFFF'
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
        ['expo-splash-screen', {
            image: './assets/images/splash-mark.png',
            imageWidth: 200,
            resizeMode: 'contain',
            backgroundColor: '#FFFFFF',
        }],
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
        // SDK 55 / RN 0.85.2 androidx.* deps require compileSdk 36. LogRocket
        // requires minSdkVersion 25. Kotlin pinned to 2.2.21 — Option C strategy.
        // See docs/android-build-strategy.md. ksp must match (handled by
        // withKspVersion plugin below).
        ['expo-build-properties', {
            android: {
                minSdkVersion: 25,
                compileSdkVersion: 36,
                targetSdkVersion: 36,
                kotlinVersion: '2.2.21',
            }
        }],
        // Belt-and-suspenders: re-stamps android.compileSdkVersion=36 and
        // android.targetSdkVersion=36 into gradle.properties. EAS build #59fcaa6b
        // showed [ExpoRootProject] compileSdk: 35 even though expo-build-properties
        // requested 36 — keeping this plugin AFTER expo-build-properties guarantees
        // useExpoVersionCatalog() sees 36 when it seeds the expoLibs catalog.
        './plugins/withForceCompileSdk',
        // Pin ksp gradle plugin to match our Kotlin (2.2.21 → 2.2.21-2.0.5). Defeats the
        // timing bug in expo-updates/android/build.gradle that resolves stale ksp
        // versions when rootProject.kotlinVersion isn't yet set at buildscript-eval time.
        // See plugin file comments for the full diagnosis.
        './plugins/withKspVersion',
        // Declares USE_FULL_SCREEN_INTENT + POST_NOTIFICATIONS + WAKE_LOCK +
        // VIBRATE + SCHEDULE_EXACT_ALARM so Notifee can wake the screen and
        // show the ride-offer panel like an incoming call when the app is
        // backgrounded or killed. See plugins/withNotifeePermissions.js.
        './plugins/withNotifeePermissions',
        // Android Auto is provided by @iternio/react-native-auto-play, which ships
        // its own merged AndroidManifest (CarAppService + permissions) and needs no
        // app-side config plugin. iOS CarPlay stays dormant: it requires an
        // Apple-granted entitlement plus scene-delegate wiring not present here.
        // See docs/carplay-android-auto.md.
        '@logrocket/react-native',
    ],
    experiments: {
        typedRoutes: true
    },
    extra: {
        eas: {
            projectId: "ddcf21cd-edb9-4edd-bd8c-bced1702dd09"
        },
        EXPO_PUBLIC_BACKEND_URL: process.env.EXPO_PUBLIC_BACKEND_URL,
        backendUrl: process.env.EXPO_PUBLIC_BACKEND_URL,
    }
});
