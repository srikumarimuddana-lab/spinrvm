/**
 * TEMPORARY DIAGNOSTIC ENTRY — startup crash trap.
 *
 * The TestFlight release build dies ~650ms after launch from an unhandled JS
 * exception thrown during initial bundle evaluation (confirmed via device
 * crash log: SIGABRT re-raised by expo-updates ErrorRecovery on
 * expo.controller.errorRecoveryQueue). The exception *message* never reaches
 * Sentry (throw happens before initErrorReporting runs) or Crashlytics (no
 * DWARF dSYM exists for the build, so the report is held unsymbolicated).
 *
 * This entry wraps bundle evaluation so instead of crashing, the device
 * SHOWS the error + stack on screen — the tester screenshots it.
 *
 * Remove (restore "main": "expo-router/entry" in package.json) once the
 * startup error is identified and fixed.
 */

const React = require('react');
const { AppRegistry, ScrollView, Text, Alert } = require('react-native');

function registerErrorScreen(prefix, e) {
  const msg = e && (e.stack || e.message) ? String(e.stack || e.message) : String(e);
  const ErrorScreen = () =>
    React.createElement(
      ScrollView,
      { style: { flex: 1, backgroundColor: '#fff' }, contentContainerStyle: { paddingTop: 80, paddingHorizontal: 16, paddingBottom: 40 } },
      React.createElement(
        Text,
        { style: { fontSize: 16, fontWeight: 'bold', marginBottom: 12, color: '#cc0000' } },
        prefix
      ),
      React.createElement(
        Text,
        { style: { fontSize: 12, fontFamily: 'Menlo', color: '#333333' }, selectable: true },
        msg
      )
    );
  AppRegistry.registerComponent('main', () => ErrorScreen);
}

// Belt-and-suspenders: catch fatal errors thrown AFTER module evaluation
// (first render, first tick). Shows an alert instead of letting RN abort.
if (global.ErrorUtils && typeof global.ErrorUtils.setGlobalHandler === 'function') {
  const previousHandler =
    typeof global.ErrorUtils.getGlobalHandler === 'function' ? global.ErrorUtils.getGlobalHandler() : null;
  global.ErrorUtils.setGlobalHandler((e, isFatal) => {
    if (isFatal) {
      try {
        Alert.alert('Spinr startup error (screenshot this)', String((e && (e.stack || e.message)) || e).slice(0, 1200));
      } catch (_) {
        // Alert unavailable this early — nothing else we can do here.
      }
      // Deliberately NOT forwarding fatals to the default handler: it would
      // RCTFatal → abort before the alert can render. Diagnostic build only.
      return;
    }
    if (previousHandler) {
      previousHandler(e, isFatal);
    }
  });
}

try {
  // Evaluates the whole app module graph (expo-router entry → app/_layout.tsx
  // → every import). A synchronous module-scope throw lands in the catch.
  require('expo-router/entry');
} catch (e) {
  registerErrorScreen('Spinr failed to start — screenshot this screen and send it to the dev team:', e);
}
