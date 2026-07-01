const { withDangerousMod } = require('@expo/config-plugins');
const { mergeContents } = require('@expo/config-plugins/build/utils/generateCode');
const fs = require('fs');
const path = require('path');

// Allow non-modular header includes inside framework modules on iOS.
//
// Why this exists: two other iOS build settings, each required on its own,
// combine to break the Firebase compile:
//   1. expo-build-properties ios.useFrameworks='static' — needed so the
//      @react-native-firebase Swift pods can import the non-modular Google pods
//      (GoogleUtilities / RecaptchaInterop / nanopb). Makes RNFB* clang
//      *framework* modules.
//   2. expo-build-properties ios.buildReactNativeFromSource=true — needed so
//      expo-dev-launcher compiles against RN 0.85.2 *source* headers (the
//      prebuilt ReactNativeCore.xcframework exposes a 4-arg
//      RCTDevMenuConfiguration the SDK-55 dev-launcher doesn't call).
//
// Together, the RNFB* framework modules #import non-modular headers —
// React/RCTConvert.h (source-built React lacks the clean module map the prebuilt
// React.xcframework had) plus FirebaseCore / nanopb / RecaptchaInterop. Clang
// treats "include of non-modular header inside framework module" as an ERROR,
// failing the archive on RNFBApp.RCTConvert_FIRApp (and, transitively,
// FirebaseSessions.sessions_nanopb).
//
// Setting CLANG_ALLOW_NON_MODULAR_INCLUDES_IN_FRAMEWORK_MODULES=YES on every pod
// target restores the tolerance the prebuilt-React path had. Applied globally
// rather than only to RNFB* so we don't have to enumerate the transitive Google
// pods (the pod-target names, e.g. RNFBApp / RNFBMessaging, are not the npm
// package names, so an explicit list is easy to get silently wrong).
//
// Runs as a dangerous mod that injects into the Expo-generated Podfile
// post_install block; idempotent via the mergeContents tag.
//
// Removal criteria: drop this once we either stop building RN from source
// (e.g. SDK 56's prebuilt core matches expo-dev-launcher, so buildReactNative-
// FromSource can go) or RNFirebase ships a clean module map under static
// frameworks. See the expo-build-properties ios block in app.config.ts.

const SNIPPET = [
    '    installer.pods_project.targets.each do |target|',
    '      target.build_configurations.each do |bc|',
    "        bc.build_settings['CLANG_ALLOW_NON_MODULAR_INCLUDES_IN_FRAMEWORK_MODULES'] = 'YES'",
    '      end',
    '    end',
].join('\n');

const withFirebaseNonModularHeaders = (config) => {
    return withDangerousMod(config, [
        'ios',
        (config) => {
            const podfilePath = path.join(
                config.modRequest.platformProjectRoot,
                'Podfile'
            );
            const contents = fs.readFileSync(podfilePath, 'utf8');
            const result = mergeContents({
                tag: 'withFirebaseNonModularHeaders',
                src: contents,
                newSrc: SNIPPET,
                anchor: /post_install do \|installer\|/,
                offset: 1,
                comment: '#',
            });
            if (result.didMerge) {
                fs.writeFileSync(podfilePath, result.contents);
            }
            return config;
        },
    ]);
};

module.exports = withFirebaseNonModularHeaders;
