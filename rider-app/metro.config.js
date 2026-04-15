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

// Watch the shared directory
config.watchFolders = [
  path.resolve(__dirname, '../shared'),
  path.resolve(__dirname, 'node_modules'),
];

// Ensure we resolve node_modules from the project root first
config.resolver.nodeModulesPaths = [
  path.resolve(__dirname, 'node_modules'),
];

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

config.resolver.resolveRequest = (context, moduleName, platform) => {
  if (platform === 'web' && WEB_STUBS[moduleName]) {
    return { filePath: WEB_STUBS[moduleName], type: 'sourceFile' };
  }
  // Fall through to the default resolver for everything else
  return context.resolveRequest(context, moduleName, platform);
};

module.exports = config;

