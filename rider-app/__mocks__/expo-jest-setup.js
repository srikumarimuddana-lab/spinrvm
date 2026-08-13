// Jest 30 + Expo SDK 54 compatibility shim.
//
// expo/src/Expo.fx.tsx -> winter/runtime.native.ts installs lazy global getters
// for several Web API polyfills via installGlobal.ts.  Each getter calls
// require('<module>') on first access.  In Jest 30 those require calls fire
// through closures that jest-runtime marks as "outside test code", throwing:
//   ReferenceError: You are trying to `import` a file outside of the scope
//                   of the test code.
//
// installGlobal.ts bails (with console.error, not a throw) when a property's
// descriptor exists and !configurable.  Pre-defining all targeted globals as
// non-configurable stops the lazy getters from ever being installed.
//
// Properties installed by runtime.native.ts (see expo/src/winter/runtime.native.ts):
//   TextDecoder, TextDecoderStream, TextEncoderStream, URL, URLSearchParams,
//   __ExpoImportMetaRegistry, structuredClone
//
// We reuse whatever Node.js already provides and fall back to no-ops for the
// ones that don't exist in the test environment.

const GLOBALS_TO_LOCK = {
  __ExpoImportMetaRegistry: { url: null },
  structuredClone:
    typeof globalThis.structuredClone !== 'undefined'
      ? globalThis.structuredClone
      : (obj) => JSON.parse(JSON.stringify(obj)),
  TextDecoder:
    typeof globalThis.TextDecoder !== 'undefined' ? globalThis.TextDecoder : class TextDecoder {},
  TextDecoderStream:
    typeof globalThis.TextDecoderStream !== 'undefined'
      ? globalThis.TextDecoderStream
      : class TextDecoderStream {},
  TextEncoderStream:
    typeof globalThis.TextEncoderStream !== 'undefined'
      ? globalThis.TextEncoderStream
      : class TextEncoderStream {},
  URL: typeof globalThis.URL !== 'undefined' ? globalThis.URL : class URL {},
  URLSearchParams:
    typeof globalThis.URLSearchParams !== 'undefined'
      ? globalThis.URLSearchParams
      : class URLSearchParams {},
};

for (const [name, fallback] of Object.entries(GLOBALS_TO_LOCK)) {
  try {
    const existing = Object.getOwnPropertyDescriptor(global, name);
    if (existing && existing.configurable === false) continue; // already locked
    Object.defineProperty(global, name, {
      value: existing ? existing.value ?? existing.get?.() ?? fallback : fallback,
      configurable: false,
      writable: true,
      enumerable: existing?.enumerable ?? false,
    });
  } catch (_) {
    // Property redefinition failed — already non-configurable or readonly; skip.
  }
}
