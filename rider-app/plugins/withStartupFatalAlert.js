const { withAppDelegate } = require('@expo/config-plugins');
const { mergeContents } = require('@expo/config-plugins/build/utils/generateCode');

// TEMPORARY DIAGNOSTIC — surface RCTFatal on-screen instead of aborting.
//
// The TestFlight build dies ~650ms after launch from a fatal thrown during JS
// bundle evaluation ("[runtime not ready]: TypeError: property is not
// writable"). Every capture channel has failed to produce the full error with
// its stack: Sentry (throws before init), Crashlytics (reason only, no JS
// stack, needs a dSYM that no longer exists), the JS-level crash trap in
// index.js (RCTFatal fires below JS), and device syslog (usbmuxd unavailable).
//
// This plugin injects native RCTSetFatalHandler / RCTSetFatalExceptionHandler
// into the generated AppDelegate BEFORE startReactNative. When a handler is
// installed, RCTFatal invokes it INSTEAD of raising the fatal exception — the
// process survives, and we present the full error text in a native alert the
// tester can screenshot. NSError userInfo carries the JS stack, so
// String(describing:) captures everything.
//
// Remove once the startup error is fixed (delete plugin + registration).

const SWIFT_BLOCK = `
    // spinr diagnostic: show startup fatals on-screen instead of aborting
    let spinrShowFatal: (String) -> Void = { details in
      DispatchQueue.main.async {
        let alert = UIAlertController(
          title: "Spinr startup error — screenshot this",
          message: String(details.prefix(1800)),
          preferredStyle: .alert
        )
        alert.addAction(UIAlertAction(title: "OK", style: .cancel, handler: nil))
        let w = UIWindow(frame: UIScreen.main.bounds)
        w.rootViewController = UIViewController()
        w.windowLevel = UIWindow.Level.alert + 1
        w.makeKeyAndVisible()
        self.window = w
        w.rootViewController?.present(alert, animated: false, completion: nil)
      }
    }
    RCTSetFatalHandler { error in
      spinrShowFatal(String(describing: error))
    }
    RCTSetFatalExceptionHandler { exception in
      spinrShowFatal(String(describing: exception))
    }
`;

const withStartupFatalAlert = (config) => {
  return withAppDelegate(config, (config) => {
    if (config.modResults.language !== 'swift') {
      throw new Error('withStartupFatalAlert expects a Swift AppDelegate');
    }
    const result = mergeContents({
      tag: 'spinr-startup-fatal-alert',
      src: config.modResults.contents,
      newSrc: SWIFT_BLOCK,
      anchor: /factory\.startReactNative\(/,
      offset: 0, // insert directly above startReactNative — handlers must be in place before JS runs
      comment: '//',
    });
    if (result.didMerge) {
      config.modResults.contents = result.contents;
    }
    return config;
  });
};

module.exports = withStartupFatalAlert;
