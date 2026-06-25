const { withAndroidManifest } = require('@expo/config-plugins');

// Notifee renders the rider's ongoing "live ride" notification. On Android 13+
// posting any notification needs POST_NOTIFICATIONS. expo-notifications usually
// declares it already, but this makes it explicit and survives if that dep is
// ever removed (the manifest Set below dedupes, so it's idempotent).
//
// The rider notification is an ordinary ongoing notification (LOW importance,
// not dismissible) — NOT a full-screen call — so it deliberately does NOT
// request USE_FULL_SCREEN_INTENT / SCHEDULE_EXACT_ALARM like the driver app's
// ride-offer plugin.
const REQUIRED_PERMISSIONS = ['android.permission.POST_NOTIFICATIONS'];

const withNotifeePermissions = (config) => {
    return withAndroidManifest(config, (cfg) => {
        const manifest = cfg.modResults.manifest;
        manifest['uses-permission'] = manifest['uses-permission'] || [];
        const existing = new Set(
            manifest['uses-permission']
                .map((p) => p && p.$ && p.$['android:name'])
                .filter(Boolean)
        );
        for (const name of REQUIRED_PERMISSIONS) {
            if (!existing.has(name)) {
                manifest['uses-permission'].push({ $: { 'android:name': name } });
            }
        }
        return cfg;
    });
};

module.exports = withNotifeePermissions;
