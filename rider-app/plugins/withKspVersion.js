const { withDangerousMod } = require('@expo/config-plugins');
const fs = require('fs');
const path = require('path');

// Pin ksp (Kotlin Symbol Processing) gradle plugin version to match our Kotlin compiler.
//
// Why this exists:
// expo-updates/android/build.gradle (line ~7) tries to pick a ksp version based on
// `rootProject["kotlinVersion"]`, but reads it AT BUILDSCRIPT EVALUATION TIME — before
// ExpoRootProjectPlugin has had a chance to set rootProject.ext.kotlinVersion. Result: the
// lookup returns null, falls through to the default branch, and resolves a stale ksp like
// `1.9.24-1.0.20`. That's incompatible with whatever Kotlin compiler is actually loaded,
// producing crashes like:
//   java.lang.NoSuchMethodError: KotlinTypeMapper$Companion.getLANGUAGE_VERSION_SETTINGS_DEFAULT()
//
// Fix: write `kspVersion=<exact ksp coord>` directly into android/gradle.properties.
// Gradle reads gradle.properties BEFORE buildscript blocks evaluate, so the value is
// available at the right time. expo-updates' build.gradle line 7-8 honors it via
// `if (rootProject.hasProperty("kspVersion")) { return rootProject["kspVersion"] }`.
//
// Version pinning rule: KSP_VERSION must match the Kotlin version pinned in:
//   1. patches/@react-native+gradle-plugin+0.85.2.patch (compiler classpath)
//   2. app.config.ts expo-build-properties.android.kotlinVersion (gradle.properties)
//
// Strategy: Option C (Kotlin 2.2.21). See docs/android-build-strategy.md.
//
// Removal criteria: delete this plugin when expo-updates' build.gradle is fixed upstream
// to use a deferred lookup (e.g., reading kspVersion via project.afterEvaluate {}) OR when
// Expo SDK ≥56 ships with a kotlinVersion → kspVersion mapping that includes our value
// in its hardcoded mapping.

// Match: Kotlin 2.2.21 → ksp 2.2.21-2.0.5 (latest stable as of 2026-05-08).
// Source: https://github.com/google/ksp/releases
const KSP_VERSION = '2.2.21-2.0.5';

const setOrAppend = (contents, key, value) => {
    const escaped = key.replace(/\./g, '\\.');
    const re = new RegExp(`^${escaped}=.*$`, 'm');
    const line = `${key}=${value}`;
    return re.test(contents)
        ? contents.replace(re, line)
        : `${contents.trimEnd()}\n${line}\n`;
};

const withKspVersion = (config) => {
    return withDangerousMod(config, [
        'android',
        async (config) => {
            const propsPath = path.join(
                config.modRequest.platformProjectRoot,
                'gradle.properties'
            );
            if (!fs.existsSync(propsPath)) return config;

            let contents = fs.readFileSync(propsPath, 'utf8');
            contents = setOrAppend(contents, 'kspVersion', KSP_VERSION);
            fs.writeFileSync(propsPath, contents);
            return config;
        },
    ]);
};

module.exports = withKspVersion;
