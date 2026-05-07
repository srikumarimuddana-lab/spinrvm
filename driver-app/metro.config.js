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

// Ensure Metro watches the shared folder
config.watchFolders = [
  path.resolve(__dirname, '../shared')
];

config.maxWorkers = Math.max(2, os.cpus().length - 1);

// Watch the shared directory
config.watchFolders = [
  path.resolve(__dirname, '../shared'),
  path.resolve(__dirname, 'node_modules'), // Ensure local node_modules are watched
];

// Ensure we resolve node_modules from the project root first
config.resolver.nodeModulesPaths = [
  path.resolve(__dirname, 'node_modules'),
];

// Block .d.ts files and the entire @types/ tree from being bundled.
// React 19 bundles its own types — the standalone @types/react package
// is only present transitively (via @types/react-test-renderer) and its
// patched main field would otherwise cause Metro to try parsing index.d.ts
// (which uses `export =` syntax that Babel can't process).
config.resolver.blockList = [
  /.*\.d\.ts$/,
  /node_modules[\\/]@types[\\/].*/,
];

// Force `react` and `react-dom` to resolve to the actual packages, not @types
config.resolver.extraNodeModules = {
  ...config.resolver.extraNodeModules,
  react: path.resolve(__dirname, 'node_modules/react'),
  'react-dom': path.resolve(__dirname, 'node_modules/react-dom'),
};

// RN 0.85 moved NativeComponent specs into src/private/ using types that
// Expo 54's Babel codegen plugin can't parse. These are relative imports
// (e.g. './VirtualViewNativeComponent') so we check context.originModulePath
// rather than the module name. The native bridge is compiled into the binary
// at build time, so stubbing the JS spec does not break OTA updates.
const NATIVE_COMPONENT_STUB = path.resolve(__dirname, '__stubs__/emptyNativeComponent.js');
config.resolver.resolveRequest = (context, moduleName, platform) => {
  // Skip @types packages during bundling — TypeScript-only, never bundled.
  // Prevents SyntaxError from `export =` in .d.ts and resolves transitive deps
  // from @types/react-test-renderer → @types/react.
  if (moduleName.startsWith('@types/')) {
    return { type: 'empty' };
  }

  const origin = context.originModulePath || '';
  const isFromRNPrivate =
    origin.includes('react-native\\src\\private') ||
    origin.includes('react-native/src/private');
  const isNativeComponentImport = moduleName.includes('NativeComponent');
  const isDirectPrivateImport =
    (moduleName.includes('/src/private/') || moduleName.includes('\\src\\private\\')) &&
    isNativeComponentImport;

  if ((isFromRNPrivate && isNativeComponentImport) || isDirectPrivateImport) {
    return { type: 'sourceFile', filePath: NATIVE_COMPONENT_STUB };
  }
  return context.resolveRequest(context, moduleName, platform);
};

module.exports = config;
