// Stub for RN 0.85 specs_DEPRECATED NativeComponent files that the Babel
// codegen can't parse. Returns a passthrough component so callers that do
// `.default` get something renderable instead of undefined.
function NativeComponentStub(props) {
  return props.children || null;
}

module.exports = NativeComponentStub;
module.exports.default = NativeComponentStub;
module.exports.Commands = new Proxy({}, { get: () => () => {} });
