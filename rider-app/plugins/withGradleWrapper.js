const { withDangerousMod } = require('@expo/config-plugins');
const fs = require('fs');
const path = require('path');

// Pin Gradle wrapper to 8.10.2.
// RN 0.85.2 ships with Gradle 9.0.0 by default, which has breaking API changes
// that break native modules using pre-2023 Gradle build scripts (react-native-maps-directions,
// @logrocket/react-native, etc.). Gradle 8.10.2 is the stable LTS used by RN 0.76-0.78
// and is fully compatible with compileSdkVersion 35, AGP 8.x, and Kotlin 2.x.
const GRADLE_VERSION = '8.10.2';

const withGradleWrapper = (config) => {
    return withDangerousMod(config, [
        'android',
        async (config) => {
            const wrapperPath = path.join(
                config.modRequest.platformProjectRoot,
                'gradle',
                'wrapper',
                'gradle-wrapper.properties'
            );
            if (!fs.existsSync(wrapperPath)) return config;

            const contents = fs.readFileSync(wrapperPath, 'utf8');
            const updated = contents.replace(
                /distributionUrl=.*/,
                `distributionUrl=https\\://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip`
            );
            fs.writeFileSync(wrapperPath, updated);
            return config;
        },
    ]);
};

module.exports = withGradleWrapper;
