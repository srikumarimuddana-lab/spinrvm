// Stubs expo/src/winter/* modules that trigger jest-runtime scope errors (Expo SDK 54).
// The __ExpoImportMetaRegistry lazy getter calls require('./ImportMetaRegistry') outside
// the test code scope; returning a safe stub prevents the ReferenceError.
module.exports = { ImportMetaRegistry: { url: null } };
