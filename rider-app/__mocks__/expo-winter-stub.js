// Jest stub for expo winter runtime polyfills.
// The winter runtime (import.meta registry, TextDecoder, URL, etc.) is not
// needed in the Jest/jsdom environment and its lazy global getter pattern
// triggers jest-runtime 30's isInsideTestCode guard.
module.exports = {};
