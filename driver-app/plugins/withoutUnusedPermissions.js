const { withAndroidManifest } = require('@expo/config-plugins');

// Strips Android permissions that a dependency declares in its own library
// manifest but that this app never exercises.
//
// The manifest merger unions every library's <uses-permission> into the final
// APK/AAB, so a module we use for one feature can drag in a permission scoped
// to a feature we don't use. Two consequences, both real:
//
//   1. Play blocks the upload when a declared permission contradicts a Play
//      Console declaration (this is how AD_ID surfaced — a hard submit
//      rejection, not a warning), or demands a written justification for a
//      sensitive permission we can't actually justify.
//   2. PIPEDA data minimisation: we shouldn't request access we don't use, and
//      CLAUDE.md is explicit that Spinr is "not a data-harvesting product".
//
// `tools:node="remove"` tells the merger to drop the library's contribution.
// It needs the tools namespace declared on <manifest>, which this plugin adds.
//
// Pass the permissions to strip via the plugin's props in app.config.ts, so the
// list — and the reason for each entry — is visible where a reviewer looks,
// rather than buried in here.
const TOOLS_NS = 'http://schemas.android.com/tools';

const withoutUnusedPermissions = (config, { permissions = [] } = {}) => {
    return withAndroidManifest(config, (cfg) => {
        if (permissions.length === 0) return cfg;

        const manifest = cfg.modResults.manifest;

        // tools:node is silently inert without the namespace on <manifest>.
        manifest.$ = manifest.$ || {};
        manifest.$['xmlns:tools'] = manifest.$['xmlns:tools'] || TOOLS_NS;

        manifest['uses-permission'] = manifest['uses-permission'] || [];

        for (const name of permissions) {
            // Drop any plain declaration first — a bare entry sitting alongside
            // the remove directive gives the merger contradictory instructions.
            manifest['uses-permission'] = manifest['uses-permission'].filter(
                (p) => !(p && p.$ && p.$['android:name'] === name)
            );

            manifest['uses-permission'].push({
                $: {
                    'android:name': name,
                    'tools:node': 'remove',
                },
            });
        }

        return cfg;
    });
};

module.exports = withoutUnusedPermissions;
