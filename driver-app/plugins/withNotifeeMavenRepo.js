const { withDangerousMod } = require('@expo/config-plugins');
const fs = require('fs');
const path = require('path');

// Add @notifee/react-native's bundled local Maven repo to android/build.gradle.
//
// Why this exists: @notifee/react-native ships its Android native module (app.notifee:core)
// as a local .aar/.pom under node_modules/@notifee/react-native/android/libs instead of
// publishing it to any public registry (Google's Maven, Maven Central, JitPack, etc). Nothing
// in autolinking adds that local folder as a Gradle repository automatically, so
// `:app:debugRuntimeClasspath` fails to resolve `app.notifee:core:+` on every local build with:
//   Could not find any matches for app.notifee:core:+ as no versions of app.notifee:core
//   are available.
// withNotifeePermissions.js (this dir) only handles the AndroidManifest permission — a
// separate concern. This plugin is the missing repository wiring.
//
// Idempotency: prebuild can run more than once against the same generated file, so this
// checks for the marker string before inserting.
const NOTIFEE_MAVEN_URL = '$rootDir/../node_modules/@notifee/react-native/android/libs';

const withNotifeeMavenRepo = (config) => {
    return withDangerousMod(config, [
        'android',
        async (config) => {
            const buildGradlePath = path.join(
                config.modRequest.platformProjectRoot,
                'build.gradle'
            );
            if (!fs.existsSync(buildGradlePath)) return config;

            let contents = fs.readFileSync(buildGradlePath, 'utf8');
            if (contents.includes(NOTIFEE_MAVEN_URL)) return config;

            const repositoriesBlock = /(allprojects\s*{\s*repositories\s*{)/;
            if (!repositoriesBlock.test(contents)) return config;

            contents = contents.replace(
                repositoriesBlock,
                `$1\n        maven { url "${NOTIFEE_MAVEN_URL}" } // @notifee/react-native's local core .aar — see withNotifeeMavenRepo.js`
            );
            fs.writeFileSync(buildGradlePath, contents);
            return config;
        },
    ]);
};

module.exports = withNotifeeMavenRepo;
