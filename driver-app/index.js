// Register Android Auto's separate AppRegistry roots before expo-router registers
// the phone root. Android Auto/DHU can cold-launch directly into the car root via
// native AppRegistry.runApplication("AndroidAuto", ...), so this must live in the
// real bundle entry rather than a route layout that may never be evaluated first.
import './car/androidAutoEntry';

// Keep Expo Router as the app's phone entry after car roots are registered.
import 'expo-router/entry';
