// Custom JS entry point.
//
// `expo-router/entry` registers the phone app's root component and routing.
// We import it first so nothing about the normal app changes, then register
// the Android Auto experience. Registration must run at bundle load so Android
// Auto can launch the app car-only (without the phone UI ever mounting).
import 'expo-router/entry';
import registerAutoPlay from './lib/androidAuto/register';

registerAutoPlay();
