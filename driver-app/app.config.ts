import { ExpoConfig, ConfigContext } from 'expo/config';

const APP_NAME = 'Spinr Driver';
const BUNDLE_ID = 'com.spinr.driver'; // driver-only ID — rider app uses com.spinr.user (no clash)
const SCHEME = 'spinr-driver';

export default ({ config }: ConfigContext): ExpoConfig => ({
    ...config,
    name: APP_NAME,
    slug: 'spinrdriver',
    // Must stay above the shipped App Store version (currently 1.0.2 from the
    // pre-rewrite app) — Apple closes released version trains, so an upload at
    // or below it is rejected at App Store Connect validation.
    version: '2.0.0',
    orientation: 'portrait',
    icon: './assets/images/icon.png',
    scheme: SCHEME,
    userInterfaceStyle: 'automatic',
    // @ts-expect-error newArchEnabled is valid Expo config but not yet typed in ExpoConfig
    newArchEnabled: true, // REQUIRED: react-native-reanimated 4 / react-native-worklets only run on the New Architecture (old arch crashed on first animated screen)
    updates: {
        url: 'https://u.expo.dev/1ed02cf4-97cb-4678-b5a2-0881f89abaa8',
    },
    // Bare workflow requires a literal string runtimeVersion (policies like
    // 'fingerprint'/'appVersion' rejected by EAS CLI). Bump manually when
    // shipping native changes that break JS-bundle compatibility. Pre-launch
    // with no production users, OTA compatibility risk is zero.
    runtimeVersion: '2.5.0', // bump from 2.4.0: expo-sqlite adds a native module for the durable trip-location outbox, so SQLite-dependent JS must never reach a pre-SQLite binary over the air. 2.4.0 isolated the react-native-screens 4.23.0 native line after 4.24.0 New-Arch/Bridgeless codegen resolved an expo-router <Screen> to a non-renderable object in release builds. Prior 2.2.0 -> 2.3.0 added react-native-webview (Stripe embedded onboarding) + Android CAMERA, plus @iternio/react-native-auto-play + react-native-nitro-modules (Android Auto).
    splash: {
        image: './assets/images/splash-blank.png',
        resizeMode: 'contain',
        backgroundColor: '#FFFFFF',
    },
    ios: ({
        supportsTablet: true,
        minimumOsVersion: '16.4', // expo-build-properties 57.x requires ios.deploymentTarget >= 16.4 (was 16.0 under 55.0.14); SDK 55 itself only required 16.0
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
        ],
        // Purpose strings — Apple rejects uploads with ITMS-90683 if any
        // dependency calls a permission-gated API without a matching string.
        // Driver app needs camera + photo library for onboarding document
        // uploads (license, insurance, vehicle registration) per the
        // Saskatchewan Transportation Act eligibility requirements, and for
        // Stripe's in-app identity verification (capturing a government ID
        // during payout setup). Location strings come from the expo-location
        // plugin block.
        infoPlist: {
            // Export compliance: Spinr Driver uses only standard HTTPS/TLS
            // (exempt encryption), so declare false. Without this, every
            // TestFlight / App Store upload blocks on a manual "does your app
            // use encryption?" answer in App Store Connect.
            ITSAppUsesNonExemptEncryption: false,
            NSCameraUsageDescription:
                'Spinr Driver uses your camera to scan and upload your driver license, vehicle insurance, and vehicle registration documents, and to verify your identity for payouts.',
            NSPhotoLibraryUsageDescription:
                'Spinr Driver accesses your photo library so you can upload existing photos of your driver license, vehicle insurance, and vehicle registration.',
            // Lets Linking.canOpenURL() detect whether Google Maps / Waze are
            // installed before deep-linking turn-by-turn navigation (Settings →
            // Navigation). Without these whitelisted, canOpenURL always returns
            // false on iOS and we'd never open the driver's chosen app.
            LSApplicationQueriesSchemes: ['comgooglemaps', 'waze'],
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
        // CAMERA is needed for in-WebView getUserMedia — Stripe's embedded
        // identity onboarding (stripe-onboarding.tsx) captures the driver's
        // government ID live in the page. (The native expo-image-picker
        // document-upload flows delegate to the system camera app via intent
        // and don't require this, but in-page getUserMedia does.) Additive to
        // the permissions auto-added by config plugins (location, notifications).
        // No RECORD_AUDIO: Stripe Identity captures images only, never audio.
        permissions: ['android.permission.CAMERA'],
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
            image: './assets/images/splash-blank.png',
            resizeMode: 'contain',
            backgroundColor: '#FFFFFF',
        }],
        'expo-font',
        'expo-sqlite',
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
            },
            ios: {
                deploymentTarget: '16.4', // match ios.minimumOsVersion; expo-build-properties 57.x hard-validates this must be >= 16.4 (was 16.0, valid under 55.0.14 -- see maybeThrowInvalidVersions in expo-build-properties/build/pluginConfig.js); Firebase pods need >= 15
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
            }
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
        // Guard against NSRangeException crash in expo-task-manager / expo-location
        // geofencing native code. CoreLocation can fire didExitRegion: with nil
        // region data during a CLConnection disconnection, crashing the app.
        // Two plugins for defense-in-depth: source-side (nil-check the CLRegion)
        // and sink-side (@try/@catch around the array insertion).
        './plugins/withGeofenceConsumerNilGuard',
        './plugins/withTaskServiceNilGuard',
        // Declares USE_FULL_SCREEN_INTENT + POST_NOTIFICATIONS + WAKE_LOCK +
        // VIBRATE + SCHEDULE_EXACT_ALARM so Notifee can wake the screen and
        // show the ride-offer panel like an incoming call when the app is
        // backgrounded or killed. See plugins/withNotifeePermissions.js.
        './plugins/withNotifeePermissions',
        // Copies the ride-offer notification sound into the native builds:
        // ride_offer.mp3 → Android res/raw (Notifee channel sound), and
        // ride_offer.caf → iOS bundle (APNs/Notifee sound). Without this the
        // ride-offer channel referenced a nonexistent resource and rang silent.
        './plugins/withRideOfferSound',
        // Adds an iOS Notification Service Extension that downloads + attaches
        // the ride-offer fare banner so the offer push shows the rich image
        // (iOS equivalent of the Android BigPicture). Backend sends the URL via
        // apns.fcm_options.image + mutable-content. NATIVE change — needs a new
        // build (and an EAS iOS build to validate; not exercised by tsc/Jest).
        //
        // TEMPORARILY DISABLED for dev/testing iOS builds: the NSE is a separate
        // signing target (com.spinr.driver.OfferCardService) that needs its own
        // Apple provisioning profile, which blocks the archive until EAS is given
        // Apple-account access to mint it. The extension is only a push-image
        // enhancement (offer pushes still arrive, just without the rich banner),
        // so it's safe to skip while testing. RE-ENABLE before the production
        // cutover and provision the extension's profile (eas credentials -> log
        // in to Apple -> set up com.spinr.driver.OfferCardService).
        // './plugins/withOfferCardNotificationService',
        // Android Auto is provided by @iternio/react-native-auto-play, which ships
        // its own merged AndroidManifest (CarAppService + permissions) and needs no
        // app-side config plugin. iOS CarPlay stays dormant: it requires an
        // Apple-granted entitlement plus scene-delegate wiring not present here.
        // See docs/carplay-android-auto.md.
        '@logrocket/react-native',
        // Meta (Facebook) app events. Same posture as the rider app: Advanced
        // Matching is sent SERVER-side via the Conversions API, so no IDFA is
        // collected, no advertiser tracking happens on-device, and
        // NSPrivacyTracking above stays false with no ATT prompt.
        //
        // NOTE: this app has its own Meta App ID and its own app dataset. The
        // separate dataset is the primary guard that keeps driver applications
        // from being reported as rider acquisitions — do not point both apps
        // at one App ID. See META_EVENTS.md.
        [
            'react-native-fbsdk-next',
            {
                appID: process.env.EXPO_PUBLIC_FB_APP_ID,
                clientToken: process.env.EXPO_PUBLIC_FB_CLIENT_TOKEN,
                displayName: APP_NAME,
                scheme: `fb${process.env.EXPO_PUBLIC_FB_APP_ID ?? ''}`,
                advertiserIDCollectionEnabled: false,
                autoLogAppEventsEnabled: true,
                isAutoInitEnabled: true,
            },
        ],
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
        // Driver app's OWN Meta app id — distinct from the rider app's.
        fbAppId: process.env.EXPO_PUBLIC_FB_APP_ID,
        fbClientToken: process.env.EXPO_PUBLIC_FB_CLIENT_TOKEN,
    }
});
