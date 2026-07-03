import { ExpoConfig, ConfigContext } from 'expo/config';

const APP_NAME = 'Spinr';
const BUNDLE_ID = 'com.spinr.user'; // rider-only ID — driver app uses com.spinr.driver (no clash)
const SCHEME = 'spinr-user';

export default ({ config }: ConfigContext): ExpoConfig => ({
    ...config,
    name: APP_NAME,
    slug: 'spinr-rider',
    // Must stay above the shipped App Store version (currently 1.0.2 from the
    // pre-rewrite app) — Apple closes released version trains, so an upload at
    // or below it is rejected at App Store Connect validation.
    version: '2.0.0',
    orientation: 'portrait',
    icon: './assets/images/icon.png',
    scheme: SCHEME,
    userInterfaceStyle: 'automatic',
    newArchEnabled: true, // REQUIRED: react-native-reanimated 4 / react-native-worklets only run on the New Architecture (old arch crashed on first animated screen)
    updates: {
        url: 'https://u.expo.dev/8f1e4f60-720e-46b0-9b71-33c13d3af043',
        // TEMPORARY DIAGNOSTIC — supersedes the earlier checkAutomatically:'NEVER'.
        // The TestFlight crash is a SIGABRT on expo.controller.errorRecoveryQueue:
        // expo-updates installs itself as the native RCTFatalExceptionHandler, so
        // when a JS fatal fires at startup its ErrorRecovery pipeline runs and
        // RE-RAISES the error as a native abort — before the JS-level crash trap
        // in index.js can render the error on screen. checkAutomatically:'NEVER'
        // only stops the update *check*; it leaves that fatal handler installed
        // AND a previously-downloaded update could still launch. `enabled: false`
        // removes expo-updates from the process entirely: the embedded (crash-trap)
        // bundle is guaranteed to run, and its ErrorUtils handler gets a clean shot
        // at any JS error. Two outcomes, both decisive:
        //   - app now launches  → the crash WAS expo-updates (bad OTA / recovery)
        //   - red error screen  → a JS startup error; screenshot reveals the message
        // Revert (delete `enabled`, restore ON_LOAD default) once the cause is fixed.
        enabled: false,
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
        // No ios.config.googleMapsApiKey on purpose: iOS uses Apple Maps. Every
        // MapView sets provider = (Platform.OS === 'android' ? PROVIDER_GOOGLE :
        // undefined), so iOS renders Apple Maps and never needs the Google SDK.
        // Setting this key makes @expo/config-plugins (ios/Maps.js) inject the
        // obsolete `react-native-google-maps` pod, which react-native-maps 1.x no
        // longer ships → `pod install` fails. Google Maps stays Android-only
        // (see android.config.googleMaps.apiKey below).
        associatedDomains: [
            'applinks:spinr.app',
            'applinks:spinr-track.app',
        ],
        // Purpose strings — Apple rejects uploads with ITMS-90683 if any
        // dependency calls a permission-gated API without a matching string.
        // Only declare keys for capabilities actually used; declaring unused
        // permissions triggers App Review questions.
        infoPlist: {
            // Export compliance: Spinr uses only standard HTTPS/TLS (exempt
            // encryption), so declare false. Without this, every TestFlight /
            // App Store upload blocks on a manual "does your app use
            // encryption?" answer in App Store Connect.
            ITSAppUsesNonExemptEncryption: false,
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
                // Compile React Native from source instead of the prebuilt
                // ReactNativeCore.xcframework. On SDK 55 the prebuilt 0.85.2 core
                // exposes a 4-arg RCTDevMenuConfiguration(...,bundleConfiguration:)
                // while expo-dev-launcher 55.0.36 still calls the 3-arg form that
                // matches the npm RN *source* headers — so against the prebuilt
                // binary the dev-launcher Swift fails to compile ("missing argument
                // for parameter 'bundleConfiguration'"). Building from source aligns
                // every RN header with what expo-dev-launcher expects. The 4-arg
                // API only lands in SDK 56; until we migrate, source build is the fix.
                buildReactNativeFromSource: true,
                // @react-native-firebase Swift pods (AppCheckCore,
                // FirebaseCoreInternal, FirebaseCrashlytics, FirebaseSessions)
                // depend on non-modular Google pods (GoogleUtilities,
                // GoogleDataTransport, nanopb, RecaptchaInterop) that can't be
                // imported from Swift when built as plain static libraries.
                // Building all pods as static *frameworks* gives them module maps
                // so the import works. This is the documented RNFirebase + Expo
                // fix; without it `pod install` fails on the static-lib error.
                useFrameworks: 'static',
            },
        }],
        // Must run AFTER expo-build-properties: injects into the generated
        // Podfile post_install so the iOS static-frameworks + source-build combo
        // above doesn't fail the Firebase compile on non-modular header includes.
        './plugins/withFirebaseNonModularHeaders',
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
