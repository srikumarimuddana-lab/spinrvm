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


// // Exclude unnecessary directories from file watching
// config.watchFolders = [__dirname];
// config.resolver.blacklistRE = /(.*)\/(__tests__|android|ios|build|dist|.git|node_modules\/.*\/android|node_modules\/.*\/ios|node_modules\/.*\/windows|node_modules\/.*\/macos)(\/.*)?$/;

// // Alternative: use a more aggressive exclusion pattern
// config.resolver.blacklistRE = /node_modules\/.*\/(android|ios|windows|macos|__tests__|\.git|.*\.android\.js|.*\.ios\.js)$/;

// Reduce the number of workers to decrease resource usage
config.maxWorkers = 2;

// Add path aliases for shared modules
config.resolver.extraNodeModules = {
  '@shared': path.resolve(__dirname, '../shared'),
};

// Ensure Metro watches the shared folder
config.watchFolders = [
  path.resolve(__dirname, '../shared')
];

// Prevent native-only modules from crashing the web bundler.
// react-native-maps imports codegenNativeCommands which doesn't exist on web.
// Returning { type: 'empty' } is the Expo-canonical no-op resolution.
const _resolveRequest = config.resolver.resolveRequest;
config.resolver.resolveRequest = (context, moduleName, platform) => {
  if (platform === 'web' && moduleName === 'react-native/Libraries/Utilities/codegenNativeCommands') {
    return { type: 'empty' };
  }
  return _resolveRequest
    ? _resolveRequest(context, moduleName, platform)
    : context.resolveRequest(context, moduleName, platform);
};

module.exports = config;
