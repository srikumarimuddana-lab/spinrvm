#!/usr/bin/env node
/**
 * Remove the nested node_modules that yarn v1 creates inside @spinr/shared
 * when it's installed via `file:../shared`. The shared package has no runtime
 * dependencies anymore (everything's a peer), but yarn 1 still installs
 * devDependencies and resolves peers into a nested tree — producing
 * `node_modules/@spinr/shared/node_modules/react-native` etc.
 *
 * Those duplicates trip up expo-doctor's "duplicate native modules" check
 * and would cause EAS Build to fail before the Android/iOS compile step.
 * Deleting the nested folder is safe because Metro resolves React Native
 * and friends from the top-level node_modules (which is where the real
 * copies live).
 *
 * Runs as part of `postinstall`. No-op if the folder doesn't exist.
 */
const fs = require('fs');
const path = require('path');

const target = path.resolve(__dirname, '..', 'node_modules', '@spinr', 'shared', 'node_modules');

if (!fs.existsSync(target)) {
    process.exit(0);
}

try {
    fs.rmSync(target, { recursive: true, force: true });
    console.log(`[dedupe-shared-nm] removed nested ${path.relative(process.cwd(), target)}`);
} catch (e) {
    console.warn(`[dedupe-shared-nm] failed to remove ${target}: ${e.message}`);
    // Non-fatal: doctor will surface the duplicates if removal failed.
}
