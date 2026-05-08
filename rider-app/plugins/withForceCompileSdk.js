const { withDangerousMod } = require('@expo/config-plugins');
const fs = require('fs');
const path = require('path');

// Force compileSdk and targetSdk into android/gradle.properties at prebuild.
//
// Why this exists, even though expo-build-properties (in app.config.ts) already
// targets the same keys: on EAS the [ExpoRootProject] log line has shown
// `compileSdk: 35` despite the config plugin writing 36 — meaning the value
// expo-build-properties wrote to gradle.properties either didn't survive or
// wasn't observable to ExpoAutolinkingSettingsExtension.useExpoVersionCatalog
// (which reads `android.compileSdkVersion` from gradle.properties to seed the
// `expoLibs` version catalog that ExpoRootProjectPlugin consumes).
//
// This plugin runs after expo-build-properties (it must be listed AFTER it in
// the plugins array). It rewrites the two SDK keys directly in gradle.properties
// so the values are guaranteed to be present when settings.gradle is evaluated.
//
// SDK 55 / RN 0.85.2 androidx.* deps (browser:1.9.0, core:1.17.0, activity:1.12.4,
// navigationevent:1.0.2) require compileSdk 36; otherwise :app:checkReleaseAarMetadata
// fails the build.
//
// Removal criteria: delete this plugin when Expo SDK ≥56 ships compileSdk 36 as default
// AND useExpoVersionCatalog() bridges gradle.properties values reliably on EAS
// (verified by a build whose [ExpoRootProject] log line shows compileSdk: 36 with this
// plugin DISABLED in app.config.ts). See docs/android-build-strategy.md.
const COMPILE_SDK = '36';
const TARGET_SDK = '36';

const setOrAppend = (contents, key, value) => {
    const escaped = key.replace(/\./g, '\\.');
    const re = new RegExp(`^${escaped}=.*$`, 'm');
    const line = `${key}=${value}`;
    return re.test(contents)
        ? contents.replace(re, line)
        : `${contents.trimEnd()}\n${line}\n`;
};

const withForceCompileSdk = (config) => {
    return withDangerousMod(config, [
        'android',
        async (config) => {
            const propsPath = path.join(
                config.modRequest.platformProjectRoot,
                'gradle.properties'
            );
            if (!fs.existsSync(propsPath)) return config;

            let contents = fs.readFileSync(propsPath, 'utf8');
            contents = setOrAppend(contents, 'android.compileSdkVersion', COMPILE_SDK);
            contents = setOrAppend(contents, 'android.targetSdkVersion', TARGET_SDK);
            fs.writeFileSync(propsPath, contents);
            return config;
        },
    ]);
};

module.exports = withForceCompileSdk;
