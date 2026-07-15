const { withXcodeProject, withDangerousMod } = require('@expo/config-plugins');
const fs = require('fs');
const path = require('path');

// Adds an iOS Notification Service Extension ("OfferCardService") so ride-offer
// pushes render the branded fare banner image (the iOS equivalent of the
// Android BigPicture). The extension source lives in
// plugins/notification-service-extension/ and is installed into the iOS project
// at prebuild; the backend sends the image via apns.fcm_options.image and sets
// mutable-content so the extension runs (see backend/features.py).
//
// IMPORTANT: this mutates the Xcode project (adds a new native target). It must
// be validated with an EAS iOS build — it cannot be exercised by Jest/tsc.

const NSE_TARGET = 'OfferCardService';
const NSE_SWIFT = 'NotificationService.swift';
const SRC_DIR = 'plugins/notification-service-extension';
const DEPLOYMENT_TARGET = '15.1';
// Apple Developer Team that signs the extension target. Xcode 14+ signs every
// target (incl. app extensions) and errors "requires a development team" if the
// .appex target has none — EAS only auto-signs the MAIN app, not config-plugin
// targets. Defaults to Spinr Mobility Inc's team; override via APPLE_TEAM_ID
// (e.g. eas.json build.<profile>.env) if the signing team ever changes.
const APPLE_TEAM_ID = process.env.APPLE_TEAM_ID || 'X8863WAU7L';

function withExtensionFiles(config) {
  return withDangerousMod(config, [
    'ios',
    (cfg) => {
      const src = path.join(cfg.modRequest.projectRoot, SRC_DIR);
      const dest = path.join(cfg.modRequest.platformProjectRoot, NSE_TARGET);
      fs.mkdirSync(dest, { recursive: true });
      const swift = path.join(src, NSE_SWIFT);
      const plist = path.join(src, 'OfferCardService-Info.plist');
      if (!fs.existsSync(swift) || !fs.existsSync(plist)) {
        throw new Error(`[withOfferCardNotificationService] missing extension sources in ${src}`);
      }
      fs.copyFileSync(swift, path.join(dest, NSE_SWIFT));
      fs.copyFileSync(plist, path.join(dest, 'Info.plist'));
      return cfg;
    },
  ]);
}

// `addTarget` stores the target name quoted ("OfferCardService"), so
// pbxTargetByName('OfferCardService') never matches — check both forms.
function targetExists(proj, name) {
  const nt = proj.hash.project.objects.PBXNativeTarget || {};
  return Object.keys(nt).some((key) => {
    const t = nt[key];
    return t && typeof t === 'object' && (t.name === name || t.name === `"${name}"`);
  });
}

function withExtensionTarget(config) {
  return withXcodeProject(config, (cfg) => {
    const proj = cfg.modResults;

    // Idempotent — config plugins can run more than once per prebuild.
    if (targetExists(proj, NSE_TARGET)) {
      return cfg;
    }

    const mainBundleId =
      (cfg.ios && cfg.ios.bundleIdentifier) || config.ios.bundleIdentifier;
    const nseBundleId = `${mainBundleId}.${NSE_TARGET}`;

    // 1. Group holding the extension's files in the project navigator,
    //    attached under the project's actual main group.
    const group = proj.addPbxGroup(['Info.plist', NSE_SWIFT], NSE_TARGET, NSE_TARGET);
    const mainGroupId = proj.getFirstProject().firstProject.mainGroup;
    proj.addToPbxGroup(group.uuid, mainGroupId);

    // 2. The app-extension target itself.
    const target = proj.addTarget(NSE_TARGET, 'app_extension', NSE_TARGET, nseBundleId);

    // 3. Build phases. Sources compiles the Swift; Resources/Frameworks empty.
    proj.addBuildPhase([NSE_SWIFT], 'PBXSourcesBuildPhase', 'Sources', target.uuid);
    proj.addBuildPhase([], 'PBXResourcesBuildPhase', 'Resources', target.uuid);
    proj.addBuildPhase([], 'PBXFrameworksBuildPhase', 'Frameworks', target.uuid);

    // 4. Embedding is already done by addTarget() above. For an 'app_extension'
    //    target, node-xcode creates the "Embed App Extensions" Copy Files phase
    //    on the main app target and adds the extension's PRODUCT reference —
    //    which addProductFile() parented under the Products group — to it.
    //
    //    Do NOT add a second embed phase here. Passing the "OfferCardService.appex"
    //    *filename* to addBuildPhase makes node-xcode mint a brand-new
    //    PBXFileReference that is never attached to any PBXGroup; that orphan is
    //    exactly what xcodeproj refuses to serialize during `pod install`, failing
    //    the EAS build with:
    //      [Xcodeproj] Consistency issue: no parent for object `OfferCardService.appex`
    //    A manual duplicate phase would also double-embed the .appex ("multiple
    //    commands produce" once the orphan is gone). One embed phase, from
    //    addTarget, is correct.

    // 5. Build settings for both Debug + Release configs of the new target.
    const xcConfigs = proj.pbxXCBuildConfigurationSection();
    Object.keys(xcConfigs).forEach((key) => {
      const bc = xcConfigs[key];
      if (
        bc &&
        bc.buildSettings &&
        bc.buildSettings.PRODUCT_NAME === `"${NSE_TARGET}"`
      ) {
        const s = bc.buildSettings;
        s.INFOPLIST_FILE = `${NSE_TARGET}/Info.plist`;
        s.PRODUCT_BUNDLE_IDENTIFIER = `"${nseBundleId}"`;
        s.IPHONEOS_DEPLOYMENT_TARGET = DEPLOYMENT_TARGET;
        s.SWIFT_VERSION = '5.0';
        s.TARGETED_DEVICE_FAMILY = '"1,2"';
        s.CODE_SIGN_STYLE = 'Automatic';
        // Give the extension target a signing team. Without this Xcode 14+ fails
        // the archive with "Signing for OfferCardService requires a development
        // team". EAS re-writes this to Manual + its managed profile once the
        // extension's credentials exist; until then this stops the hard error.
        s.DEVELOPMENT_TEAM = APPLE_TEAM_ID;
        s.CLANG_ENABLE_MODULES = 'YES';
        s.SWIFT_OPTIMIZATION_LEVEL = s.SWIFT_OPTIMIZATION_LEVEL || '"-Onone"';
        s.MARKETING_VERSION = '1.0';
        s.CURRENT_PROJECT_VERSION = '1';
      }
    });

    return cfg;
  });
}

module.exports = (config) => withExtensionTarget(withExtensionFiles(config));
