// Stub out expo/src/winter to prevent Jest 30's isInsideTestCode guard from
// firing. The polyfills installed by this module (TextDecoder, URL, etc.) are
// natively available in Node 22 so no-op is safe for unit tests.
