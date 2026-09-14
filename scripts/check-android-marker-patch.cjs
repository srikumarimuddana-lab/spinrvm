#!/usr/bin/env node
'use strict';

// Static contract check, not a substitute for an Android build/road test.
// Default: check committed patches. --installed: also check postinstall output.
// Run from any directory: node scripts/check-android-marker-patch.cjs [--installed]
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const base = 'node_modules/react-native-maps/';
const java = 'android/src/main/java/';
const cpp = 'android/src/main/jni/react/renderer/components/RNMapsSpecs/';
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8').replace(/\r\n/g, '\n');
const nativeStart = `diff --git a/${base}src/specs/NativeComponentMarker.ts`;
const patches = ['driver-app', 'rider-app'].map((app) =>
  read(`${app}/patches/react-native-maps+1.27.2.patch`));
for (const patch of patches) assert.ok(patch.includes(nativeStart), 'native patch missing');
assert.equal(patches[0].slice(patches[0].indexOf(nativeStart)),
  patches[1].slice(patches[1].indexOf(nativeStart)), 'driver/rider native patch drift');

function checkContract(source, label) {
  const has = (file, regex) => assert.match(source(file), regex, `${label}: ${file}`);
  const spec = 'src/specs/NativeComponentMarker.ts';
  has(spec, /markerRotation\?: WithDefault<Float, 0>/);
  has(spec, /flat\?: WithDefault<boolean, false>/);
  has('src/MapMarker.tsx', /Platform\.OS === 'android'\s*\? \{markerRotation: this\.props\.rotation \?\? 0, rotation: undefined\}\s*: \{\}/);
  const manager = `${java}com/rnmaps/fabric/MarkerManager.java`;
  has(manager, /getFloat\("markerRotation", 0\)/);
  has(manager, /setMarkerRotation\(MapMarker view, float value\)\s*\{\s*view\.setRotation\(value\)/);
  has(manager, /setFlat\(MapMarker view, boolean value\)\s*\{\s*view\.setFlat\(value\)/);
  const iface = `${java}com/facebook/react/viewmanagers/RNMapsMarkerManagerInterface.java`;
  has(iface, /void setMarkerRotation\(T view, float value\)/);
  has(iface, /void setFlat\(T view, boolean value\)/);
  const delegate = `${java}com/facebook/react/viewmanagers/RNMapsMarkerManagerDelegate.java`;
  has(delegate, /case "markerRotation":\s*mViewManager\.setMarkerRotation\(/);
  has(delegate, /case "flat":\s*mViewManager\.setFlat\(/);
  has(`${cpp}Props.h`, /Float markerRotation\{0\.0\}/);
  has(`${cpp}Props.h`, /bool flat\{false\}/);
  has(`${cpp}Props.cpp`, /markerRotation\(convertRawProp\(context, rawProps, "markerRotation", sourceProps\.markerRotation, \{0\.0\}\)\)/);
  has(`${cpp}Props.cpp`, /flat\(convertRawProp\(context, rawProps, "flat", sourceProps\.flat, \{false\}\)\)/);
  // RN reconciliation emits diffs; constructor-only plumbing silently fails.
  has(`${cpp}Props.cpp`, /if \(markerRotation != oldProps->markerRotation\)\s*\{\s*result\["markerRotation"\] = markerRotation/);
  has(`${cpp}Props.cpp`, /if \(flat != oldProps->flat\)\s*\{\s*result\["flat"\] = flat/);
}

for (const [i, app] of ['driver-app', 'rider-app'].entries()) {
  const sections = new Map();
  for (const section of patches[i].split(/(?=^diff --git )/m)) {
    const filename = section.match(/^\+\+\+ b\/node_modules\/react-native-maps\/(.+)$/m)?.[1];
    if (!filename) continue;
    // Reconstruct the post-patch excerpts, excluding removed lines/headers.
    sections.set(filename, section.split('\n')
      .filter((line) => line.startsWith(' ') || (line.startsWith('+') && !line.startsWith('+++')))
      .map((line) => line.slice(1)).join('\n'));
  }
  checkContract((file) => sections.get(file) ?? '', `${app} patch`);
  const config = read(`${app}/app.config.ts`);
  const androidRuntime = app === 'driver-app' ? '2.8.0' : '2.2.0';
  assert.ok(config.slice(config.indexOf('    android: {')).includes(`runtimeVersion: '${androidRuntime}'`),
    `${app}: missing native runtime fence`);
  if (process.argv.includes('--installed')) {
    checkContract((file) => read(`${app}/${base}${file}`), `${app} installed dependency`);
  }
}
console.log('Android marker native property chain and sibling patches: OK');
if (!process.argv.includes('--installed')) {
  console.log('Installed dependencies not checked. Re-run with --installed after dependency installation.');
}
