// Stub for RN 0.85 specs_DEPRECATED / VirtualView NativeComponent files that the
// Babel codegen plugin can't parse. Metro's resolveRequest (metro.config.js)
// swaps those broken native-component specs for this file.
//
// It MUST export a renderable React component, not a bare object. Some of the
// stubbed specs (e.g. the codegen native view behind ActivityIndicator) are
// actually rendered under the New Architecture; if this module is `{}` (an
// object), React throws "Element type is invalid: expected a string/… but got:
// object" the moment such a component mounts — which crashed every screen of
// the driver app at launch (BrandSplash's ActivityIndicator was the first to
// hit it). A passthrough component renders harmlessly instead. Mirrors the
// working rider-app stub.
const React = require('react');

function NativeComponentStub(props) {
  return props.children || null;
}

module.exports = NativeComponentStub;
module.exports.default = NativeComponentStub;
module.exports.Commands = new Proxy({}, { get: () => () => {} });
