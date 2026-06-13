// Custom JS entry point.
//
// expo-router/entry registers the phone app's root + routing (unchanged).
// registerAutoPlay() wires the Android Auto experience and must run at bundle
// load: Android Auto / DHU can cold-launch the JS context car-only, with the
// phone UI (a route layout) never mounting — so this lives in the real bundle
// entry. Order between the two is irrelevant for @iternio/react-native-auto-play:
// it registers its headless task on import and only acts on a 'didConnect' event.
//
// registerBackgroundMessageHandlers() must also run at bundle load: ride
// offers are data-only FCM messages, and when the app is killed Android
// delivers them via a headless JS launch where no route module (including
// app/_layout.tsx) ever executes. If the handler isn't registered here, the
// offer is silently dropped and the driver never sees it.
import 'expo-router/entry';
import registerAutoPlay from './lib/androidAuto/register';
import { registerBackgroundMessageHandlers } from './services/backgroundMessaging';

registerAutoPlay();
registerBackgroundMessageHandlers();
