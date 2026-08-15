const { withAndroidManifest } = require('@expo/config-plugins');

// Strips com.google.android.gms.permission.AD_ID from the merged manifest.
//
// react-native-fbsdk-next declares AD_ID in its own library manifest, so the
// Android manifest merger pulls it into our APK/AAB unconditionally — even
// though app.config.ts sets `advertiserIDCollectionEnabled: false` and Meta
// Advanced Matching runs SERVER-side via the Conversions API, so no advertiser
// ID is ever read on-device.
//
// The mismatch is a hard submit blocker, not a warning. Play rejects the
// upload outright:
//   "This release includes the com.google.android.gms.permission.AD_ID
//    permission but your declaration on Play Console says your app doesn't use
//    advertising ID."
//
// Two ways to resolve it: flip the Play Console declaration to "uses
// advertising ID", or stop declaring the permission. We do the latter — the
// declaration is the accurate one. Per CLAUDE.md, Spinr is "not a
// data-harvesting product… never add third-party ad SDKs or behavioral
// retargeting", and PIPEDA data minimisation means not requesting access we
// don't use.
//
// `tools:node="remove"` instructs the manifest merger to drop the permission
// contributed by any library, which requires the tools namespace on <manifest>.
// Meta app events continue to work; only the advertiser-ID read is removed,
// and that was already disabled at runtime.
const AD_ID_PERMISSION = 'com.google.android.gms.permission.AD_ID';
const TOOLS_NS = 'http://schemas.android.com/tools';

const withoutAdIdPermission = (config) => {
    return withAndroidManifest(config, (cfg) => {
        const manifest = cfg.modResults.manifest;

        // tools:node is inert without the namespace declared on <manifest>.
        manifest.$ = manifest.$ || {};
        manifest.$['xmlns:tools'] = manifest.$['xmlns:tools'] || TOOLS_NS;

        manifest['uses-permission'] = manifest['uses-permission'] || [];

        // Drop any plain declaration first — a bare entry alongside the remove
        // directive leaves the merger with contradictory instructions.
        manifest['uses-permission'] = manifest['uses-permission'].filter(
            (p) => !(p && p.$ && p.$['android:name'] === AD_ID_PERMISSION)
        );

        manifest['uses-permission'].push({
            $: {
                'android:name': AD_ID_PERMISSION,
                'tools:node': 'remove',
            },
        });

        return cfg;
    });
};

module.exports = withoutAdIdPermission;
