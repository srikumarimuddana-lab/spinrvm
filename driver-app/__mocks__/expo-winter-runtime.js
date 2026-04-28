// Jest does not support the Expo SDK 54 winter (WinterCG) runtime polyfills.
// This no-op mock prevents the runtime.native module from executing
// require('./ImportMetaRegistry') inside Jest's module sandbox.
