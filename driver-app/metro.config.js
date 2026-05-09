// metro.config.js
const { getDefaultConfig } = require("expo/metro-config");
const os = require('os');
const path = require('path');
const { FileStore } = require('metro-cache');
const config = getDefaultConfig(__dirname);

// Use a stable on-disk store (shared across web/android)
const root = process.env.METRO_CACHE_ROOT || path.join(__dirname, '.metro-cache');
config.cacheStores = [
  new FileStore({ root: path.join(root, 'cache') }),
];

// Add path aliases for shared modules
config.resolver.extraNodeModules = {
  '@shared': path.resolve(__dirname, '../shared'),
};

config.maxWorkers = Math.max(2, os.cpus().length - 1);

// Watch the shared directory so edits in shared/ trigger rebuilds
config.watchFolders = [
  path.resolve(__dirname, '../shared'),
  path.resolve(__dirname, 'node_modules'),
];

// Ensure we resolve node_modules from the project root first.
// This is the PRIMARY mechanism for deduplicating React — Metro checks
// these paths BEFORE walking up the directory tree from the importer.
config.resolver.nodeModulesPaths = [
  path.resolve(__dirname, 'node_modules'),
];

// Block duplicate React-family packages in shared/node_modules from being
// bundled. Without this, code in shared/ that imports 'react' could resolve
// to shared/node_modules/react (a different version) instead of
// driver-app/node_modules/react. Two copies of React = "Invalid hook call".
//
// Also block .d.ts files and @types/ (React 19 bundles its own types).
const escapedSharedNM = path
  .resolve(__dirname, '../shared/node_modules')
  .replace(/[\\\/]/g, '[\\\\/]');
config.resolver.blockList = [
  /.*\.d\.ts$/,
  /node_modules[\\/]@types[\\/].*/,
  // Block singleton packages from shared/node_modules:
  new RegExp(escapedSharedNM + '[\\\\/]react[\\\\/]'),
  new RegExp(escapedSharedNM + '[\\\\/]react-native[\\\\/]'),
  new RegExp(escapedSharedNM + '[\\\\/]react-dom[\\\\/]'),
  new RegExp(escapedSharedNM + '[\\\\/]scheduler[\\\\/]'),
  new RegExp(escapedSharedNM + '[\\\\/]react-refresh[\\\\/]'),
  new RegExp(escapedSharedNM + '[\\\\/]react-is[\\\\/]'),
];

// RN 0.85 moved some NativeComponent specs into src/private/specs_DEPRECATED/
// using codegen types that Expo's Babel plugin can't parse. These codegen specs
// return non-renderable objects under the New Architecture (Bridgeless) in
// Expo Go, causing "Element type is invalid: got object" crashes.
//
// IMPORTANT: Only stub imports from `specs_DEPRECATED/` — the other directory
// `src/private/components/` contains WORKING components (ScrollView wrappers,
// SafeAreaView) that import from Libraries/ using NativeComponentRegistry.get.
// Stubbing those would break ScrollView rendering.
const NATIVE_COMPONENT_STUB = path.resolve(__dirname, '__stubs__/emptyNativeComponent.js');
config.resolver.resolveRequest = (context, moduleName, platform) => {
  // Skip @types packages during bundling — TypeScript-only, never bundled.
  if (moduleName.startsWith('@types/')) {
    return { type: 'empty' };
  }

  // Stub broken codegen specs in specs_DEPRECATED only.
  const origin = context.originModulePath || '';
  const isFromSpecsDeprecated =
    origin.includes('specs_DEPRECATED') &&
    (origin.includes('react-native\\src\\private') ||
     origin.includes('react-native/src/private'));
  const isNativeComponentImport = moduleName.includes('NativeComponent');

  // Also catch direct imports into specs_DEPRECATED from outside
  const isDirectSpecsDeprecatedImport =
    (moduleName.includes('specs_DEPRECATED') ||
     moduleName.includes('specs_DEPRECATED')) &&
    isNativeComponentImport;

  if ((isFromSpecsDeprecated && isNativeComponentImport) || isDirectSpecsDeprecatedImport) {
    return { type: 'sourceFile', filePath: NATIVE_COMPONENT_STUB };
  }
  return context.resolveRequest(context, moduleName, platform);
};

module.exports = config;
