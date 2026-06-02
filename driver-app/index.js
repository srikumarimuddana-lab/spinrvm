// Custom JS entry point.
//
// expo-router/entry registers the phone app's root + routing (unchanged).
// registerAutoPlay() wires the Android Auto experience and must run at bundle
// load: Android Auto / DHU can cold-launch the JS context car-only, with the
// phone UI (a route layout) never mounting — so this lives in the real bundle
// entry. Order between the two is irrelevant for @iternio/react-native-auto-play:
// it registers its headless task on import and only acts on a 'didConnect' event.
import 'expo-router/entry';
import registerAutoPlay from './lib/androidAuto/register';

registerAutoPlay();
