const { withDangerousMod } = require('@expo/config-plugins');
const fs = require('fs');
const path = require('path');
const { globSync } = require('glob');

// Patches EXTaskService.m (expo-task-manager) to guard against NSRangeException
// crashes in -[EXTaskService executeTask:withData:withError:].
//
// Root cause: when CoreLocation fires a geofence-related callback during a
// CLConnectionServer disconnection/reconnect cycle, EXTaskService's pending-
// event NSMutableArray can be mutated concurrently (producer on CoreLocation's
// callback queue, consumer on the main queue). The index computed from a stale
// `count` lands past the array's current bounds, throwing NSRangeException.
// See: https://github.com/expo/expo/issues/28728
//
// Spinr amplifies the bug because its geofence recovery loop re-arms on every
// exit, producing a high rate of didDetermineState: callbacks.
//
// Fix: wrap the body of executeTask:withData:withError: in @try/@catch so the
// out-of-bounds insertion is caught and logged instead of crashing the app.
//
// Removal criteria: drop this once expo-task-manager ships a version with its
// own thread-safe guard (track https://github.com/expo/expo/issues/28728).

const withTaskServiceNilGuard = (config) => {
    return withDangerousMod(config, [
        'ios',
        (config) => {
            const iosRoot = config.modRequest.platformProjectRoot;

            // Search both Pods (post-pod-install) and node_modules (pre-pod-install
            // or bare workflow).
            const searchDirs = [
                path.join(iosRoot, 'Pods'),
                path.join(iosRoot, '..', 'node_modules'),
            ];

            // Objective-C only. A .swift variant is deliberately NOT patched:
            // generating Swift here could not be compile-verified, and a broken
            // injection is worse than none. The zero-match warning below is the
            // signal to revisit if expo-task-manager migrates to Swift.
            const patterns = ['**/EXTaskService.m'];

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
                    '[withTaskServiceNilGuard] WARNING: No EXTaskService.m found — ' +
                    'the @try/@catch crash guard will NOT be active in this build. ' +
                    'Verify expo-task-manager is installed and pod install has run.',
                );
                return config;
            }

            let patchedCount = 0;

            for (const filePath of candidates) {
                let src = fs.readFileSync(filePath, 'utf8');

                // Already patched — idempotent.
                if (src.includes('/* EXTaskService-nil-guard */')) {
                    patchedCount++;
                    continue;
                }

                // Objective-C only. Match on the selector parts with flexible
                // parameter names — SDK versions have used both `taskName` and
                // `task` for the first parameter.
                const methodSig =
                    /-\s*\(void\)\s*executeTask:\s*\([^)]*\)\s*\w+\s+withData:\s*\([^)]*\)\s*\w+\s+withError:\s*\([^)]*\)\s*\w+\s*\{/;

                const sigMatch = src.match(methodSig);
                if (!sigMatch) {
                    console.warn(
                        `[withTaskServiceNilGuard] Could not match ObjC executeTask:withData:withError: signature in ${path.relative(iosRoot, filePath)}`,
                    );
                    continue;
                }

                // Compute the method boundary on the UNMODIFIED source. Scanning
                // after injecting `@try {` would count that extra brace, so the
                // scan would sail past the method's own close and land on the
                // enclosing @implementation's — emitting a @catch in the wrong
                // place and producing uncompilable source.
                const sigStart = src.indexOf(sigMatch[0]);
                const bodyStart = sigStart + sigMatch[0].length; // just past the opening `{`
                let depth = 1;
                let methodEnd = -1;
                for (let i = bodyStart; i < src.length; i++) {
                    if (src[i] === '{') depth++;
                    else if (src[i] === '}') {
                        depth--;
                        if (depth === 0) { methodEnd = i; break; }
                    }
                }

                if (methodEnd < 0) {
                    console.warn(
                        `[withTaskServiceNilGuard] Could not find the end of executeTask:withData:withError: in ${path.relative(iosRoot, filePath)} — not patching`,
                    );
                    continue;
                }

                // Splice the tail first so bodyStart stays a valid index.
                src =
                    src.slice(0, methodEnd) +
                    '\n  } @catch (NSException *ex) {\n' +
                    '    NSLog(@"[EXTaskService-nil-guard] Caught %@: %@", ex.name, ex.reason);\n' +
                    '  }\n' +
                    src.slice(methodEnd);
                src =
                    src.slice(0, bodyStart) +
                    '\n  /* EXTaskService-nil-guard */\n  @try {' +
                    src.slice(bodyStart);

                fs.writeFileSync(filePath, src);
                patchedCount++;
                console.log(
                    `[withTaskServiceNilGuard] Patched ${path.relative(iosRoot, filePath)}`,
                );
            }

            if (patchedCount === 0) {
                console.warn(
                    '[withTaskServiceNilGuard] WARNING: Found EXTaskService source file(s) but ' +
                    'could not match the method signature in any of them. The @try/@catch ' +
                    'crash guard will NOT be active in this build.',
                );
            }

            return config;
        },
    ]);
};

module.exports = withTaskServiceNilGuard;
