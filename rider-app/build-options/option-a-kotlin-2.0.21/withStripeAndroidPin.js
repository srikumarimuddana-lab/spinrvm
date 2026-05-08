const { withDangerousMod } = require('@expo/config-plugins');
const fs = require('fs');
const path = require('path');

// Pin Stripe Android SDK to 21.6.0 (the last release compiled with Kotlin 2.0.21).
//
// Why: @stripe/stripe-react-native@0.63.0 declares StripeSdk_stripeVersion=23.3.+
// in its android/gradle.properties. Stripe Android SDK 23.x was compiled with
// Kotlin 2.2.x (metadata version 2.2.0), which our compiler at Kotlin 2.0.21
// cannot read — produces "Module was compiled with an incompatible version of
// Kotlin. The binary version of its metadata is 2.2.0, expected version is
// 2.0.0." Per stripe-android CHANGELOG, 21.7.0 bumped Kotlin from 2.0.21 to
// 2.1.10, so 21.6.0 is the highest 2.0.21-compatible version.
//
// stripe-react-native checks rootProject.ext.has("stripeVersion") FIRST before
// project.properties["StripeSdk_stripeVersion"], so writing to gradle.properties
// at the root project level is the override path.
//
// Potential compile error: stripe-react-native 0.63.0 (paired with 23.x) may
// reference Stripe SDK APIs that didn't exist in 21.6.0. If the build fails with
// "unresolved reference" errors in stripe-react-native source, the next step is
// to downgrade @stripe/stripe-react-native to 0.45.0 (the version Stripe paired
// with 21.6.+ originally).
const STRIPE_VERSION = '21.6.+';

const setOrAppend = (contents, key, value) => {
    const escaped = key.replace(/\./g, '\\.');
    const re = new RegExp(`^${escaped}=.*$`, 'm');
    const line = `${key}=${value}`;
    return re.test(contents)
        ? contents.replace(re, line)
        : `${contents.trimEnd()}\n${line}\n`;
};

const withStripeAndroidPin = (config) => {
    return withDangerousMod(config, [
        'android',
        async (config) => {
            const propsPath = path.join(
                config.modRequest.platformProjectRoot,
                'gradle.properties'
            );
            if (!fs.existsSync(propsPath)) return config;

            let contents = fs.readFileSync(propsPath, 'utf8');
            contents = setOrAppend(contents, 'StripeSdk_stripeVersion', STRIPE_VERSION);
            fs.writeFileSync(propsPath, contents);
            return config;
        },
    ]);
};

module.exports = withStripeAndroidPin;
