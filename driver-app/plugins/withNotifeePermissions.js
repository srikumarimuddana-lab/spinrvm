const { withAndroidManifest } = require('@expo/config-plugins');

// Adds Android permissions required by Notifee for full-screen ride-offer
// notifications. Notifee itself adds USE_FULL_SCREEN_INTENT via manifest merger
// on most setups, but Expo prebuild has occasionally stripped it after merge
// passes. Declaring it here is belt-and-suspenders.
//
// USE_FULL_SCREEN_INTENT (Android 14+): on devices targeting SDK 34+ this is a
// runtime appop permission. For non-call/non-alarm apps Google grants it by
// default at install on first launch, but the user can revoke via
// Settings → Apps → Special access → Full-screen intent. The Notifee service
// handles the user-facing flow if it's denied.
//
// SCHEDULE_EXACT_ALARM (Android 12+): needed if we ever schedule offer reminders
// at exact times; harmless to declare and avoids a later manifest churn.
const REQUIRED_PERMISSIONS = [
    'android.permission.USE_FULL_SCREEN_INTENT',
    'android.permission.POST_NOTIFICATIONS',
    'android.permission.WAKE_LOCK',
    'android.permission.VIBRATE',
    'android.permission.SCHEDULE_EXACT_ALARM',
];

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
