#!/usr/bin/env node
/**
 * Remove a redundant nested copy of @babel/helper-compilation-targets that
 * yarn v1's linker sometimes creates under
 * node_modules/@babel/plugin-transform-classes/node_modules/@babel/ —
 * at the EXACT SAME version already correctly installed at the top level
 * (both currently 7.29.7; the lockfile has only one merged resolution
 * range for this package, so there is no real version conflict to nest
 * for). The nested copy is missing its own nested `lru-cache` dependency
 * (the top-level copy has one, correctly pinned to the old ^5.1.1
 * constructor-style API this Babel version needs), while the repo's
 * top-level `lru-cache` resolves to a newer v10+ package with a different
 * (named-export) API. Node's `require("lru-cache")` from inside the
 * nested copy then walks up and picks the incompatible top-level v10+
 * package, which breaks at babel-transform time with:
 *   "TypeError: [BABEL] .../react-native-env.js: _lruCache is not a
 *   constructor"
 * — before any jest test can even run.
 *
 * Deleting the redundant nested folder is safe: Node's module resolution
 * then walks up to the top-level @babel/helper-compilation-targets copy,
 * which is the identical version and already has its own correctly
 * nested lru-cache@5.1.1. Confirmed fixes `yarn jest` (verified against
 * `__tests__/app/becomeDriverScreen.test.tsx`, previously failing at
 * suite-load time for every driver-app test, not just that one).
 *
 * Runs as part of `postinstall`, after dedupe-shared-nm.js. No-op if the
 * folder doesn't exist (e.g. a future yarn/dependency update stops
 * creating it).
 */
const fs = require('fs');
const path = require('path');

const target = path.resolve(
    __dirname, '..', 'node_modules', '@babel', 'plugin-transform-classes',
    'node_modules', '@babel', 'helper-compilation-targets',
);

if (!fs.existsSync(target)) {
    process.exit(0);
}

try {
    fs.rmSync(target, { recursive: true, force: true });
    console.log(`[dedupe-babel-helper-compilation-targets] removed nested ${path.relative(process.cwd(), target)}`);
} catch (e) {
    console.warn(`[dedupe-babel-helper-compilation-targets] failed to remove ${target}: ${e.message}`);
    // Non-fatal: jest will surface the "_lruCache is not a constructor"
    // error at suite-load time if removal failed, which is at least loud
    // and diagnosable rather than silently masked.
}
