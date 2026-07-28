const { withDangerousMod } = require('@expo/config-plugins');
const fs = require('fs');
const path = require('path');
const { globSync } = require('glob');

// Patches EXGeofencingTaskConsumer.m (expo-location) to nil-check the CLRegion
// before building the geofence event data dictionary.
//
// The crash path: CoreLocation fires -locationManager:didDetermineState:forRegion:
// during a CLConnectionServer disconnection/reconnect cycle. The CLRegion may
// carry nil properties (identifier, center). EXGeofencingTaskConsumer passes
// them through to EXTaskService without checking, feeding nil values into an
// NSMutableArray insertion that throws NSRangeException.
//
// This is the source-side fix (prevent bad data from reaching EXTaskService).
// withTaskServiceNilGuard.js is the sink-side fix (@try/@catch at the insertion
// point). Both are needed for defense-in-depth.
//
// Removal criteria: drop when expo-location ships its own guard
// (track https://github.com/expo/expo/issues/28728).

const withGeofenceConsumerNilGuard = (config) => {
    return withDangerousMod(config, [
        'ios',
        (config) => {
            const iosRoot = config.modRequest.platformProjectRoot;

            const searchDirs = [
                path.join(iosRoot, 'Pods'),
                path.join(iosRoot, '..', 'node_modules'),
            ];

            // Objective-C only — see the note in withTaskServiceNilGuard.js.
            // The Swift form of this guard cannot be written safely without
            // knowing whether the region parameter is optional, and it could
            // not be compile-verified here.
            const patterns = ['**/EXGeofencingTaskConsumer.m'];

            const candidates = [];
            for (const dir of searchDirs) {
                if (!fs.existsSync(dir)) continue;
                for (const pattern of patterns) {
                    candidates.push(
                        ...globSync(pattern, { cwd: dir, absolute: true }),
                    );
                }
            }

            if (candidates.length === 0) {
                console.warn(
                    '[withGeofenceConsumerNilGuard] WARNING: No EXGeofencingTaskConsumer.m found — ' +
                    'the nil-region guard will NOT be active in this build. ' +
                    'Verify expo-location is installed and pod install has run.',
                );
                return config;
            }

            let patchedCount = 0;

            for (const filePath of candidates) {
                let src = fs.readFileSync(filePath, 'utf8');

                if (src.includes('/* EXGeofenceConsumer-nil-guard */')) {
                    patchedCount++;
                    continue;
                }

                const methodSig =
                    /(-\s*\(void\)\s*executeTaskWithRegion:\s*\([^)]*\)\s*region\s+eventType:\s*\([^)]*\)\s*eventType\s*\{)/;

                if (!methodSig.test(src)) {
                    console.warn(
                        `[withGeofenceConsumerNilGuard] Could not match ObjC executeTaskWithRegion:eventType: in ${path.relative(iosRoot, filePath)}`,
                    );
                    continue;
                }

                src = src.replace(methodSig, [
                    '$1',
                    '  /* EXGeofenceConsumer-nil-guard */',
                    '  if (region == nil || region.identifier == nil) {',
                    '    NSLog(@"[EXGeofenceConsumer-nil-guard] Skipping task — region or identifier is nil");',
                    '    return;',
                    '  }',
                ].join('\n'));

                fs.writeFileSync(filePath, src);
                patchedCount++;
                console.log(
                    `[withGeofenceConsumerNilGuard] Patched ${path.relative(iosRoot, filePath)}`,
                );
            }

            if (patchedCount === 0) {
                console.warn(
                    '[withGeofenceConsumerNilGuard] WARNING: Found EXGeofencingTaskConsumer source ' +
                    'file(s) but could not match the method signature in any of them. The ' +
                    'nil-region guard will NOT be active in this build.',
                );
            }

            return config;
        },
    ]);
};

module.exports = withGeofenceConsumerNilGuard;
