// Custom JS entry point.
//
// IMPORTANT: this file uses require() rather than ES imports on purpose, for
// execution ordering. ES `import` declarations are hoisted and all run before
// any statement in the module, so `import 'expo-router/entry'` would register
// the root component (AppRegistry.registerComponent) BEFORE a later
// registerBackgroundMessageHandlers() call could run. @react-native-firebase/
// messaging requires setBackgroundMessageHandler to be installed before
// AppRegistry registration: for a killed-state, data-only ride offer, native
// can invoke the headless ReactNativeFirebaseMessagingHeadlessTask as soon as
// the bundle finishes evaluating, and if the JS handler isn't set yet the
// offer is silently dropped — the exact bug this fixes. require() gives us
// guaranteed top-to-bottom order so the FCM handler wins the race.

// 0. Silence the @react-native-firebase v22+ modular-API deprecation warnings.
//    We still use the namespaced API (messaging()/appCheck()/crashlytics()) in
//    shared/services/firebase.ts; on v24 every call logs a "Please use getApp()
//    … migrating-to-v22" warning. These are cosmetic only — the namespaced API
//    keeps working until the next major release. This flag (read by the SDK at
//    @react-native-firebase/app/lib/common) suppresses the console spam without
//    changing any behaviour. Must be set before the first Firebase call below,
//    which is why it leads the entry file. Remove once firebase.ts is migrated
//    to the modular API.
globalThis.RNFB_SILENCE_MODULAR_DEPRECATION_WARNINGS = true;

// 1. Register the headless FCM + Notifee background handlers FIRST, before the
//    app component is registered. Ride offers are data-only FCM messages; when
//    the app is killed Android delivers them via a headless JS launch where no
//    route module (including app/_layout.tsx) ever executes.
require('./services/backgroundMessaging').registerBackgroundMessageHandlers();

// 2. Register the Expo Router root + routing (runs AppRegistry).
require('expo-router/entry');

// 3. Android Auto. Registers its headless task on import and only acts on a
//    'didConnect' event, so its order is irrelevant — it just must run at
//    bundle load: Android Auto / DHU can cold-launch the JS context car-only,
//    with the phone UI (a route layout) never mounting.
//
//    iOS guard: Android Auto is Android-only (iOS CarPlay isn't wired). Loading
//    this tree on iOS pulls in the react-native-auto-play / nitro-modules
//    runtime for no benefit. Rider — which has no Android Auto — runs fine while
//    driver crashes at first render on the New-Architecture release build, so
//    this native integration is the leading suspect. Skip it entirely off
//    Android; requires a NATIVE rebuild to take effect (not an OTA).
if (require('react-native').Platform.OS === 'android') {
  require('./lib/androidAuto/register').default();
}
