const { withDangerousMod } = require('@expo/config-plugins');
const { mergeContents } = require('@expo/config-plugins/build/utils/generateCode');
const fs = require('fs');
const path = require('path');

// iOS static-frameworks compatibility for @react-native-firebase.
//
// Why this exists: two other iOS build settings, each required on its own,
// combine to break the Firebase compile:
//   1. expo-build-properties ios.useFrameworks='static' — needed so the
//      @react-native-firebase Swift pods can import the non-modular Google pods
//      (GoogleUtilities / RecaptchaInterop / nanopb). Makes every pod a clang
//      *framework* module.
//   2. expo-build-properties ios.buildReactNativeFromSource=true — needed so
//      expo-dev-launcher compiles against RN 0.85.2 *source* headers (the
//      prebuilt ReactNativeCore.xcframework exposes a 4-arg
//      RCTDevMenuConfiguration the SDK-55 dev-launcher doesn't call).
//
// This plugin makes two Podfile edits:
//
// (a) pre_install: force build_type = static_library for the RNFB pods.
//     Their *public* headers textually include React headers (e.g.
//     RNFBMessaging+AppDelegate.h imports <React/RCTBridgeModule.h>), which
//     makes their own framework modules invalid: when RNFBApp was compiled as a
//     framework module the React declarations got absorbed into it, and
//     RNFBMessaging then failed with "declaration of 'RCTPromiseRejectBlock'
//     must be imported from module 'RNFBApp.RNFBAppModule' before it is
//     required". As plain static libraries they compile textually, the way
//     RNFB was designed. This replicates what expo-modules-autolinking's
//     installer.rb does for ExpoModulesCore/expo-dev-menu (same reason, same
//     singleton-method technique) — but that path is gated on
//     RCT_USE_PREBUILT_RNCORE=1, so with buildReactNativeFromSource=true the
//     expo-build-properties `forceStaticLinking` option silently no-ops and we
//     must apply it ourselves.
//
// (b) post_install: CLANG_ALLOW_NON_MODULAR_INCLUDES_IN_FRAMEWORK_MODULES=YES
//     on all pod targets. Source-built React headers are not covered by a
//     module map from other pods' compile context, so any remaining ObjC
//     framework-module pod that textually includes React headers
//     (react-native-maps, react-native-webview, netinfo, …) would otherwise
//     error with "include of non-modular header inside framework module".
//     Safe for the RNFB family because (a) removed them from module builds —
//     this setting is what caused the absorption problem when they were still
//     framework modules, so (a) and (b) must ship together.
//
// Runs as a dangerous mod on the Expo-generated Podfile; idempotent via
// mergeContents tags.
//
// Removal criteria: drop this once we either stop building RN from source
// (e.g. SDK 56's prebuilt core matches expo-dev-launcher, so buildReactNative-
// FromSource can go and forceStaticLinking works again) or RNFirebase ships
// headers that don't publicly include React core.

// Pod target names (from each package's .podspec) — NOT npm package names.
const RNFB_PODS = "['RNFBApp', 'RNFBMessaging', 'RNFBCrashlytics', 'RNFBAppCheck']";

const PRE_INSTALL_SNIPPET = [
    `  pre_install do |installer|`,
    `    installer.pod_targets.each do |pod|`,
    `      if ${RNFB_PODS}.include?(pod.name)`,
    `        Pod::UI.puts "[withFirebaseNonModularHeaders] Forcing static_library build type for #{pod.name}"`,
    `        def pod.build_type`,
    `          Pod::BuildType.static_library`,
    `        end`,
    `      end`,
    `    end`,
    `  end`,
].join('\n');

const POST_INSTALL_SNIPPET = [
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
            let contents = fs.readFileSync(podfilePath, 'utf8');

            // (a) pre_install hook — inserted immediately BEFORE post_install.
            const preResult = mergeContents({
                tag: 'withFirebaseNonModularHeaders-preinstall',
                src: contents,
                newSrc: PRE_INSTALL_SNIPPET,
                anchor: /post_install do \|installer\|/,
                offset: 0,
                comment: '#',
            });
            if (preResult.didMerge) {
                contents = preResult.contents;
            }

            // (b) build setting — inserted just INSIDE post_install.
            const postResult = mergeContents({
                tag: 'withFirebaseNonModularHeaders',
                src: contents,
                newSrc: POST_INSTALL_SNIPPET,
                anchor: /post_install do \|installer\|/,
                offset: 1,
                comment: '#',
            });
            if (postResult.didMerge) {
                contents = postResult.contents;
            }

            fs.writeFileSync(podfilePath, contents);
            return config;
        },
    ]);
};

module.exports = withFirebaseNonModularHeaders;
