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
            // or bare workflow). Also search for .swift sources — expo-task-manager
            // has been migrating modules to Swift since SDK 53.
            const searchDirs = [
                path.join(iosRoot, 'Pods'),
                path.join(iosRoot, '..', 'node_modules'),
            ];

            const patterns = ['**/EXTaskService.m', '**/EXTaskService.swift'];

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
                    '[withTaskServiceNilGuard] WARNING: No EXTaskService.m or .swift found — ' +
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

                if (filePath.endsWith('.swift')) {
                    // Swift source — wrap the body of executeTask in do/catch.
                    // Match any Swift method containing "executeTask" in its
                    // signature (parameter names vary across SDK versions).
                    const swiftSig =
                        /(func\s+executeTask\s*\([^)]*\)\s*(?:throws\s*)?(?:->\s*\S+\s*)?\{)/;
                    if (!swiftSig.test(src)) {
                        console.warn(
                            `[withTaskServiceNilGuard] Could not match Swift executeTask signature in ${path.relative(iosRoot, filePath)}`,
                        );
                        continue;
                    }

                    src = src.replace(swiftSig, [
                        '$1',
                        '    /* EXTaskService-nil-guard */',
                        '    do {',
                    ].join('\n'));

                    // Find the method's closing brace and wrap with catch.
                    const sigMatch = src.match(swiftSig);
                    if (!sigMatch) continue;
                    const methodStart = src.indexOf(sigMatch[0]);
                    let braceDepth = 0;
                    let methodEnd = -1;
                    let inMethod = false;
                    for (let i = methodStart; i < src.length; i++) {
                        if (src[i] === '{') { braceDepth++; inMethod = true; }
                        else if (src[i] === '}') {
                            braceDepth--;
                            if (inMethod && braceDepth === 0) { methodEnd = i; break; }
                        }
                    }
                    if (methodEnd > 0) {
                        src =
                            src.slice(0, methodEnd) +
                            '\n    } catch {\n' +
                            '      NSLog("[EXTaskService-nil-guard] Caught error in executeTask: \\(error)")\n' +
                            '    }\n' +
                            src.slice(methodEnd);
                    }

                    fs.writeFileSync(filePath, src);
                    patchedCount++;
                    console.log(
                        `[withTaskServiceNilGuard] Patched (Swift) ${path.relative(iosRoot, filePath)}`,
                    );
                    continue;
                }

                // Objective-C (.m) source.
                // Strategy: match the method signature with flexible parameter names.
                // SDK versions have used both `taskName` and `task` as the first param.
                // We match on the selector parts only: executeTask: / withData: / withError:
                const methodSig =
                    /(-\s*\(void\)\s*executeTask:\s*\([^)]*\)\s*\w+\s+withData:\s*\([^)]*\)\s*\w+\s+withError:\s*\([^)]*\)\s*\w+\s*\{)/;

                if (!methodSig.test(src)) {
                    console.warn(
                        `[withTaskServiceNilGuard] Could not match ObjC executeTask:withData:withError: signature in ${path.relative(iosRoot, filePath)}`,
                    );
                    continue;
                }

                src = src.replace(methodSig, [
                    '$1',
                    '  /* EXTaskService-nil-guard */',
                    '  @try {',
                ].join('\n'));

                // Close the @try block before the method's closing brace.
                const sigMatch = src.match(methodSig);
                if (!sigMatch) continue;

                const methodStart = src.indexOf(sigMatch[0]);
                let braceDepth = 0;
                let methodEnd = -1;
                let inMethod = false;

                for (let i = methodStart; i < src.length; i++) {
                    if (src[i] === '{') {
                        braceDepth++;
                        inMethod = true;
                    } else if (src[i] === '}') {
                        braceDepth--;
                        if (inMethod && braceDepth === 0) {
                            methodEnd = i;
                            break;
                        }
                    }
                }

                if (methodEnd > 0) {
                    src =
                        src.slice(0, methodEnd) +
                        '\n  } @catch (NSException *ex) {\n' +
                        '    NSLog(@"[EXTaskService-nil-guard] Caught %@: %@", ex.name, ex.reason);\n' +
                        '  }\n' +
                        src.slice(methodEnd);
                }

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
