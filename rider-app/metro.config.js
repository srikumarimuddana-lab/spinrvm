// metro.config.js
const { getDefaultConfig } = require("expo/metro-config");
const path = require('path');
const { FileStore } = require('metro-cache');

const config = getDefaultConfig(__dirname);

// Use a stable on-disk store (shared across web/android)
const root = process.env.METRO_CACHE_ROOT || path.join(__dirname, '.metro-cache');
config.cacheStores = [
  new FileStore({ root: path.join(root, 'cache') }),
];

// Reduce the number of workers to decrease resource usage
config.maxWorkers = 2;

// Add path aliases for shared modules (mirrors driver-app setup)
config.resolver.extraNodeModules = {
  '@shared': path.resolve(__dirname, '../shared'),
};

// Watch the shared directory
config.watchFolders = [
  path.resolve(__dirname, '../shared'),
  path.resolve(__dirname, 'node_modules'),
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
  // Prevent shared/node_modules from providing duplicate React/RN copies
  /\.\.[\\/]shared[\\/]node_modules[\\/](react|react-native|react-dom)[\\/].*/,
];

// Force `react`, `react-dom`, and `react-native` to resolve from rider-app's
// node_modules — prevents shared/ from loading its own duplicate copy.
config.resolver.extraNodeModules = {
  ...config.resolver.extraNodeModules,
  react: path.resolve(__dirname, 'node_modules/react'),
  'react-dom': path.resolve(__dirname, 'node_modules/react-dom'),
  'react-native': path.resolve(__dirname, 'node_modules/react-native'),
};

// Disable Metro's package-"exports" resolution (default-ON since SDK 53).
// With it on, @sentry/core resolves to its ESM build (build/esm/*), whose
// module-namespace bindings are frozen under Hermes; Sentry's module-scope
// writes to them throw "[runtime not ready]: TypeError: property is not
// writable" and abort the app at launch (release-only; dev tolerates it).
// Falling back to legacy main-field resolution loads Sentry's CJS build
// (build/cjs/*) instead. Verified via bundle diff that @sentry/core is the ONLY
// package whose resolution changes, so this is safe. See expo/expo#36589.
config.resolver.unstable_enablePackageExports = false;

// ── Web build: stub native-only packages ──────────────────────────────────
// react-native-maps and react-native-maps-directions are native-only.
// On web, Metro resolves them to thin stubs so `expo export --platform web`
// compiles without errors. Stubs render a "use mobile app" placeholder.
// This resolveRequest ONLY activates when platform === 'web'; native builds
// are completely unaffected.
const WEB_STUBS = {
  'react-native-maps': path.resolve(__dirname, 'web/stubs/react-native-maps.js'),
  'react-native-maps-directions': path.resolve(__dirname, 'web/stubs/react-native-maps-directions.js'),
};

// RN 0.85 moved NativeComponent specs into src/private/ using types that
// Expo 54's Babel codegen plugin can't parse. Stub them out — the native
// bridge is compiled into the binary at build time, so this doesn't affect
// OTA updates.
const NATIVE_COMPONENT_STUB = path.resolve(__dirname, '__stubs__/emptyNativeComponent.js');

config.resolver.resolveRequest = (context, moduleName, platform) => {
  // Skip @types packages during bundling — TypeScript-only, never bundled.
  // Prevents SyntaxError from `export =` in .d.ts and resolves transitive deps
  // from @types/react-test-renderer → @types/react.
  if (moduleName.startsWith('@types/')) {
    return { type: 'empty' };
  }

  if (platform === 'web') {
    // Stub file-based native-only packages (react-native-maps, etc.)
    if (WEB_STUBS[moduleName]) {
      return { filePath: WEB_STUBS[moduleName], type: 'sourceFile' };
    }
    // Return an empty module for react-native internal native-only helpers
    // (e.g. codegenNativeCommands) imported transitively by packages like
    // @stripe/stripe-react-native that don't web-guard their native specs.
    if (moduleName === 'react-native/Libraries/Utilities/codegenNativeCommands') {
      return { type: 'empty' };
    }
  }

  // Stub the entire VirtualView directory — RN 0.85 added an experimental
  // VirtualViewExperimentalNativeComponent whose codegen spec uses nested
  // Readonly<{}> inside DirectEventHandler that Expo 55 can't parse. VirtualView.js
  // also uses Flow `component` syntax unsupported by the Babel preset here.
  // The whole feature is behind unstable_VirtualView and unused by Expo apps.
  if (
    moduleName.includes('VirtualViewExperimentalNativeComponent') ||
    moduleName.includes('VirtualViewNativeComponent') ||
    (moduleName.includes('virtualview') && moduleName.includes('VirtualView'))
  ) {
    return { type: 'sourceFile', filePath: NATIVE_COMPONENT_STUB };
  }

  // Stub ONLY specs_DEPRECATED NativeComponent files — the Babel codegen plugin
  // (Expo SDK 55) can't parse their Flow type syntax. Don't stub anything else
  // in src/private/ (ScrollView wrappers, SafeAreaView, NativeComponentRegistry)
  // — those are plain JS the dev client needs at runtime.
  const isSpecsDeprecated = moduleName.includes('specs_DEPRECATED');
  const isNativeComponentImport = moduleName.includes('NativeComponent');

  if (isSpecsDeprecated && isNativeComponentImport) {
    return { type: 'sourceFile', filePath: NATIVE_COMPONENT_STUB };
  }

  // Fall through to the default resolver for everything else
  return context.resolveRequest(context, moduleName, platform);
};

module.exports = config;
