const { withDangerousMod, withXcodeProject, IOSConfig } = require('@expo/config-plugins');
const fs = require('fs');
const path = require('path');

// Installs the ride-offer notification sound into both native projects at
// prebuild time. Without this, the Notifee channel sound 'ride_offer'
// (services/notifeeService.ts) and the APNs `sound: ride_offer.caf` the
// backend sends for dispatch pushes both point at files that don't exist in
// the build, so ride-offer notifications ring silent.
//
// Why not the expo-notifications plugin `sounds` option: it copies every
// listed file to BOTH platforms, and shipping ride_offer.mp3 + ride_offer.caf
// together would collide in Android res/raw (two files compiling to the same
// `ride_offer` resource name). Android needs the mp3 (it can't play CAF);
// iOS needs the caf (notification sounds must be PCM in aiff/wav/caf).
//
// Android: assets/sounds/ride_offer.mp3 → android/app/src/main/res/raw/
//          (referenced by channel sound name 'ride_offer'; raw resource
//          names forbid hyphens, hence the underscore filename)
// iOS:     assets/sounds/ride_offer.caf → ios/<project>/ride_offer.caf,
//          registered as an Xcode resource so it lands in the app bundle
//          (regenerate via scripts/generate_ride_offer_caf.py)

const ANDROID_SOUND = 'ride_offer.mp3';
const IOS_SOUND = 'ride_offer.caf';

function withAndroidRideOfferSound(config) {
    return withDangerousMod(config, [
        'android',
        (cfg) => {
            const src = path.resolve(cfg.modRequest.projectRoot, 'assets/sounds', ANDROID_SOUND);
            if (!fs.existsSync(src)) {
                throw new Error(`[withRideOfferSound] missing ${src} — the ride-offer channel would be silent`);
            }
            const rawDir = path.join(cfg.modRequest.platformProjectRoot, 'app/src/main/res/raw');
            fs.mkdirSync(rawDir, { recursive: true });
            fs.copyFileSync(src, path.join(rawDir, ANDROID_SOUND));
            return cfg;
        },
    ]);
}

function withIosRideOfferSound(config) {
    return withXcodeProject(config, (cfg) => {
        const src = path.resolve(cfg.modRequest.projectRoot, 'assets/sounds', IOS_SOUND);
        if (!fs.existsSync(src)) {
            throw new Error(`[withRideOfferSound] missing ${src} — run scripts/generate_ride_offer_caf.py`);
        }
        const projectName = cfg.modRequest.projectName;
        const sourceRoot = IOSConfig.Paths.getSourceRoot(cfg.modRequest.projectRoot);
        fs.copyFileSync(src, path.join(sourceRoot, IOS_SOUND));
        const projectFilePath = `${projectName}/${IOS_SOUND}`;
        if (!cfg.modResults.hasFile(projectFilePath)) {
            IOSConfig.XcodeUtils.addResourceFileToGroup({
                filepath: projectFilePath,
                groupName: projectName,
                project: cfg.modResults,
                isBuildFile: true,
            });
        }
        return cfg;
    });
}

const withRideOfferSound = (config) =>
    withIosRideOfferSound(withAndroidRideOfferSound(config));

module.exports = withRideOfferSound;
