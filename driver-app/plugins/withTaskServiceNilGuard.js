const { withDangerousMod } = require('@expo/config-plugins');
const fs = require('fs');
const path = require('path');
const { globSync } = require('glob');

// Patches EXTaskService.m (expo-task-manager) to guard against NSRangeException
// crashes in -[EXTaskService executeTask:withData:withError:].
//
// Root cause: when CoreLocation fires a geofence exit during a CLConnection
// disconnection event, the CLRegion object can carry nil properties.
// EXGeofencingTaskConsumer passes those through to EXTaskService, which inserts
// them into an NSMutableArray — and -[__NSArrayM insertObject:atIndex:] throws
// NSRangeException on nil. The crash happens in native code before the JS task
// handler runs, so no JS-side guard can catch it.
//
// Fix: wrap the body of executeTask:withData:withError: in @try/@catch so the
// nil insertion is caught and logged instead of crashing the app.
//
// Removal criteria: drop this once expo-task-manager ships a version with its
// own nil guard (track https://github.com/expo/expo/issues/28728).

const withTaskServiceNilGuard = (config) => {
    return withDangerousMod(config, [
        'ios',
        (config) => {
            const iosRoot = config.modRequest.platformProjectRoot;
            // EXTaskService.m lives inside Pods after pod install. Its exact path
            // varies by Expo SDK — search the Pods tree.
            const podsDir = path.join(iosRoot, 'Pods');

            // Also check node_modules path (bare workflow without pods)
            const candidates = [
                ...globSync('**/EXTaskService.m', { cwd: podsDir, absolute: true }),
                ...globSync('**/EXTaskService.m', {
                    cwd: path.join(iosRoot, '..', 'node_modules'),
                    absolute: true,
                }),
            ];

            for (const filePath of candidates) {
                let src = fs.readFileSync(filePath, 'utf8');

                // Already patched — idempotent.
                if (src.includes('/* EXTaskService-nil-guard */')) continue;

                // Strategy: wrap the *entire body* of executeTask:withData:withError:
                // in @try/@catch. We match the method signature and inject a @try
                // immediately after the opening brace, then close it before the
                // closing brace. Because the method body varies across SDK versions,
                // we use a surgical regex that matches the method signature line and
                // the first opening brace, then wraps everything up to the method's
                // closing brace.
                //
                // Simpler alternative that works across versions: find the method
                // and inject a nil-coalescence at the top. If `data` is nil, replace
                // it with an empty dictionary so downstream insertObject: never
                // receives nil.
                const methodSig =
                    /(-\s*\(void\)\s*executeTask:\s*\(\s*nonnull\s+NSString\s*\*\s*\)\s*taskName\s+withData:\s*\(\s*NSDictionary\s*\*\s*\)\s*data\s+withError:\s*\(\s*NSError\s*\*\s*\)\s*error\s*\{)/;
                const altMethodSig =
                    /(-\s*\(void\)\s*executeTask:\s*\([^)]*\)\s*taskName\s+withData:\s*\([^)]*\)\s*data\s+withError:\s*\([^)]*\)\s*error\s*\{)/;

                const sig = methodSig.test(src) ? methodSig : altMethodSig;
                if (!sig.test(src)) continue;

                src = src.replace(sig, [
                    '$1',
                    '  /* EXTaskService-nil-guard */',
                    '  @try {',
                    '  if (data == nil) { data = @{}; }',
                    '  if (error == nil && data.count == 0) { return; }',
                ].join('\n'));

                // Close the @try block before the method's closing brace.
                // Find the LAST closing brace that belongs to this method.
                // We locate the method start, then scan for the matching close.
                const sigMatch = src.match(sig);
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
                console.log(
                    `[withTaskServiceNilGuard] Patched ${path.relative(iosRoot, filePath)}`
                );
            }

            return config;
        },
    ]);
};

module.exports = withTaskServiceNilGuard;
