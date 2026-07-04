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
    // spinr diagnostic: show startup fatals on-screen instead of aborting.
    // Renders the error text directly as a window's root view (NOT an alert
    // presentation, which can silently fail this early in launch) and copies
    // the full text to the clipboard so the tester can paste it.
    let spinrShowFatal: (String) -> Void = { details in
      DispatchQueue.main.async {
        UIPasteboard.general.string = details
        let vc = UIViewController()
        vc.view.backgroundColor = UIColor.white
        let tv = UITextView(frame: vc.view.bounds.insetBy(dx: 12, dy: 60))
        tv.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        tv.isEditable = false
        tv.font = UIFont.monospacedSystemFont(ofSize: 12, weight: .regular)
        tv.textColor = UIColor.red
        tv.backgroundColor = UIColor.white
        tv.text = "SPINR STARTUP ERROR — screenshot this (already copied to clipboard)\\n\\n" + details
        vc.view.addSubview(tv)
        let w = UIWindow(frame: UIScreen.main.bounds)
        w.rootViewController = vc
        w.windowLevel = UIWindow.Level.alert + 1
        w.makeKeyAndVisible()
        self.window = w
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
