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
    newArchEnabled: true, // REQUIRED: react-native-reanimated 4 / react-native-worklets only run on the New Architecture (old arch crashed on first animated screen)
    updates: {
        url: 'https://u.expo.dev/8f1e4f60-720e-46b0-9b71-33c13d3af043',
    },
    // Bare workflow requires a literal string runtimeVersion (policies like
    // 'fingerprint'/'appVersion' rejected by EAS CLI). Bump manually when
    // shipping native changes that break JS-bundle compatibility. Pre-launch
    // with no production users, OTA compatibility risk is zero.
    runtimeVersion: '2.0.0', // bumped from 1.0.0: New Architecture is a native/JS-bundle break — old-arch installs must not pull this OTA
    splash: {
        image: './assets/images/splash-blank.png',
        resizeMode: 'contain',
        backgroundColor: '#FFFFFF',
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
        // Purpose strings — Apple rejects uploads with ITMS-90683 if any
        // dependency calls a permission-gated API without a matching string.
        // Only declare keys for capabilities actually used; declaring unused
        // permissions triggers App Review questions.
        infoPlist: {
            NSCameraUsageDescription:
                'Spinr uses your camera so you can take a profile photo for your rider account.',
            NSPhotoLibraryUsageDescription:
                'Spinr accesses your photo library so you can choose an existing image as your profile photo.',
        },
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
            backgroundColor: '#FFFFFF'
        },
        // Force adjustResize so the window shrinks when the soft keyboard opens
        // (complements the KeyboardAvoidingView behavior="padding" our forms use
        // — under resize the KAV measures a shrunk frame and adds ~0 padding, so
        // there is no double-adjust). The padding behavior is what actually
        // lifts inputs on the current builds; this just makes resize the mode
        // once a native rebuild ships. Note: this is a native AndroidManifest
        // setting — it does NOT take effect over OTA, only in a new build.
        softwareKeyboardLayoutMode: 'resize',
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
        ['expo-location', {
            locationWhenInUsePermission: 'Spinr needs your location to show nearby drivers and confirm your pickup.',
        }],
        ['@stripe/stripe-react-native', {
            merchantIdentifier: 'merchant.com.spinr.user',
            enableGooglePay: true,
        }],
        'expo-font',
        'expo-image',
        'expo-secure-store',
        'expo-web-browser',
        ['expo-splash-screen', {
            image: './assets/images/splash-blank.png',
            resizeMode: 'contain',
            backgroundColor: '#FFFFFF',
        }],
        '@react-native-firebase/app',
        '@react-native-firebase/messaging',
        '@react-native-firebase/crashlytics',
        '@react-native-firebase/app-check',
        // Notifee renders the ongoing "live ride" notification (Android). Declares
        // POST_NOTIFICATIONS for Android 13+; see plugins/withNotifeePermissions.js.
        './plugins/withNotifeePermissions',
        // SDK 55 / RN 0.85.2 androidx.* deps require compileSdk 36 (build tools
        // 36.0.0 provisioned by EAS). LogRocket requires minSdkVersion 25.
        // Kotlin pinned to 2.2.21 — Option C strategy. See docs/android-build-strategy.md
        // for full context. Summary: Stripe SDK 23.3+ is built with Kotlin 2.2.21 metadata,
        // so we bump our compiler to match rather than pin Stripe back. ksp must match
        // (handled by withKspVersion plugin below).
        ['expo-build-properties', {
            android: {
                minSdkVersion: 25,
                compileSdkVersion: 36,
                targetSdkVersion: 35,
                kotlinVersion: '2.2.21',
            },
            // Voltra Live Activities require iOS 16.4+ (the activity APIs).
            ios: {
                deploymentTarget: '16.4',
            },
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
        '@logrocket/react-native',
        // Sentry native crash capture + automatic sourcemap upload at EAS build
        // time. organization/project/url come from build env; the auth token is
        // read from SENTRY_AUTH_TOKEN by sentry-cli (never commit it). Runtime JS
        // error capture is wired separately via initErrorReporting()
        // (EXPO_PUBLIC_SENTRY_DSN). Upload step no-ops gracefully when env unset.
        [
            '@sentry/react-native/expo',
            {
                organization: process.env.SENTRY_ORG,
                project: process.env.SENTRY_PROJECT,
                url: process.env.SENTRY_URL ?? 'https://sentry.io/',
            },
        ],
        // iOS Live Activity (Voltra). enablePushNotifications: true (Phase 3 —
        // was false in the Phase 0 spike) so the backend can push ActivityKit
        // updates to the activity while the app is backgrounded/killed.
        // groupIdentifier = App Group shared with the activity extension.
        [
            '@use-voltra/ios-client',
            {
                groupIdentifier: `group.${BUNDLE_ID}`,
                enablePushNotifications: true,
            },
        ],
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
