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

// expo-sqlite's web implementation (used by utils/tripLocationOutbox.ts for
// the offline trip-location queue) loads its wa-sqlite engine as a .wasm
// asset. Metro's default assetExts doesn't include 'wasm', so `expo export
// --platform web` fails to resolve it without this — this is Expo's own
// documented Metro config requirement for expo-sqlite web support.
config.resolver.assetExts.push('wasm');

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

// Disable Metro's package-"exports" resolution (default-ON since SDK 53).
// With it on, @sentry/core resolves to its ESM build (build/esm/*), whose
// module-namespace bindings are frozen under Hermes; Sentry's module-scope
// writes to them throw "[runtime not ready]: TypeError: property is not
// writable" and abort the app at launch (release-only; dev tolerates it).
// Falling back to legacy main-field resolution loads Sentry's CJS build
// (build/cjs/*) instead. Verified via bundle diff that @sentry/core is the ONLY
// package whose resolution changes, so this is safe. See expo/expo#36589.
config.resolver.unstable_enablePackageExports = false;

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
// Force all imports of @tanstack/react-query to a single file. The package
// ships `react-native: src/index.ts` AND `exports.import.default:
// build/modern/index.js`; without forcing one, Metro picks src for direct
// app imports but build/modern for the compiled @tanstack/react-query-
// persist-client. Two evaluations of QueryClientProvider.* → two distinct
// QueryClientContext instances → "No QueryClient set" at any consumer.
const RQ_FORCED_PATH = path.resolve(
  __dirname,
  'node_modules/@tanstack/react-query/build/modern/index.js',
);

config.resolver.resolveRequest = (context, moduleName, platform) => {
  // Skip @types packages during bundling — TypeScript-only, never bundled.
  if (moduleName.startsWith('@types/')) {
    return { type: 'empty' };
  }

  if (moduleName === '@tanstack/react-query') {
    return { type: 'sourceFile', filePath: RQ_FORCED_PATH };
  }

  // index.js (our custom entry, required for FCM-handler registration
  // ordering — see its own top comment) does a bare `require('react-native')`
  // to read Platform.OS for the Android Auto guard. Being the bundle's own
  // entry file rather than a node_modules import, this bare require resolves
  // straight to the real native `react-native/index.js` instead of getting
  // redirected to react-native-web on web builds, which then pulls in the
  // Fabric renderer (ReactNativePrivateInitializeCore) that has no web
  // implementation and aborts `expo export --platform web`. Force the
  // redirect explicitly for this one case; native builds are unaffected.
  if (platform === 'web' && moduleName === 'react-native') {
    return context.resolveRequest(context, 'react-native-web', platform);
  }

  // Stub broken codegen specs:
  //   - specs_DEPRECATED/* (codegen-only, no runtime impl under New Arch)
  //   - private/components/virtualview/* (RN 0.85 codegen the Expo Babel
  //     plugin can't parse: "Unable to determine event arguments for
  //     onModeChange"). VirtualView is an experimental list optimization
  //     not used by app code, so stubbing the spec is safe.
  const origin = context.originModulePath || '';
  const isFromSpecsDeprecated =
    origin.includes('specs_DEPRECATED') &&
    (origin.includes('react-native\\src\\private') ||
     origin.includes('react-native/src/private'));
  const isFromVirtualView =
    origin.includes('private\\components\\virtualview') ||
    origin.includes('private/components/virtualview');
  const isNativeComponentImport = moduleName.includes('NativeComponent');

  // Also catch direct imports of those broken-spec subtrees from outside.
  const isDirectBrokenSpecImport =
    (moduleName.includes('specs_DEPRECATED') ||
     moduleName.includes('virtualview') ||
     moduleName.includes('VirtualView')) &&
    isNativeComponentImport;

  if (
    ((isFromSpecsDeprecated || isFromVirtualView) && isNativeComponentImport) ||
    isDirectBrokenSpecImport
  ) {
    return { type: 'sourceFile', filePath: NATIVE_COMPONENT_STUB };
  }
  return context.resolveRequest(context, moduleName, platform);
};

module.exports = config;
