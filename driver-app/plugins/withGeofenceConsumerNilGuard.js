const { withDangerousMod } = require('@expo/config-plugins');
const fs = require('fs');
const path = require('path');
const { globSync } = require('glob');

// Patches EXGeofencingTaskConsumer.m (expo-location) to nil-check the CLRegion
// before building the geofence event data dictionary.
//
// The crash path: CoreLocation fires -locationManager:didExitRegion: during a
// CLConnection disconnection. The CLRegion may carry nil properties (identifier,
// center). EXGeofencingTaskConsumer passes them to EXTaskService without
// checking, causing NSRangeException in -[__NSArrayM insertObject:atIndex:].
//
// Fix: guard the region properties with nil checks before calling executeTask.
// If the region is nil or its identifier is nil, skip the task execution.
//
// This is the source-side fix (prevent nil data from being created).
// withTaskServiceNilGuard.js is the sink-side fix (catch nil data at insertion).
// Both are needed for defense-in-depth.

const withGeofenceConsumerNilGuard = (config) => {
    return withDangerousMod(config, [
        'ios',
        (config) => {
            const iosRoot = config.modRequest.platformProjectRoot;
            const podsDir = path.join(iosRoot, 'Pods');

            const candidates = [
                ...globSync('**/EXGeofencingTaskConsumer.m', { cwd: podsDir, absolute: true }),
                ...globSync('**/EXGeofencingTaskConsumer.m', {
                    cwd: path.join(iosRoot, '..', 'node_modules'),
                    absolute: true,
                }),
            ];

            for (const filePath of candidates) {
                let src = fs.readFileSync(filePath, 'utf8');

                if (src.includes('/* EXGeofenceConsumer-nil-guard */')) continue;

                // Patch executeTaskWithRegion:eventType: to nil-check the region.
                // Match the method signature and inject a guard after the opening brace.
                const methodSig =
                    /(-\s*\(void\)\s*executeTaskWithRegion:\s*\([^)]*\)\s*region\s+eventType:\s*\([^)]*\)\s*eventType\s*\{)/;

                if (!methodSig.test(src)) continue;

                src = src.replace(methodSig, [
                    '$1',
                    '  /* EXGeofenceConsumer-nil-guard */',
                    '  if (region == nil || region.identifier == nil) {',
                    '    NSLog(@"[EXGeofenceConsumer-nil-guard] Skipping task — region or identifier is nil");',
                    '    return;',
                    '  }',
                ].join('\n'));

                fs.writeFileSync(filePath, src);
                console.log(
                    `[withGeofenceConsumerNilGuard] Patched ${path.relative(iosRoot, filePath)}`
                );
            }

            return config;
        },
    ]);
};

module.exports = withGeofenceConsumerNilGuard;
