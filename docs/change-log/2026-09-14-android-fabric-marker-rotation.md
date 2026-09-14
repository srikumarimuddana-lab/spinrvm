# Android Fabric marker rotation — native follow-up to #5389

Date: 2026-09-14. Author: Codex. Domain: drivers. Surfaces: Android driver, Android Auto, Android rider. Branch: codex/android-fabric-marker-rotation.

## Evidence and root cause

User reports the marker moving sideways on Android phone and Android Auto after PR #5389, while iOS works. The installed react-native-maps 1.27.2 selects RNMapsMarker/Fabric on Android. Its codegen schema and shipped Android C++ props omit both map rotation and flat. Initial Java options alone cannot repair fields absent from the schema; live heading updates lack an explicit native property contract. The existing JS heading changes therefore do not fix that missing contract.

Correction from native review: BaseViewManager.setRotation DOES dispatch to MapMarker.setRotation (which updates Google Maps); it is not inherently an Android-view-only rotation. The defect addressed here is the missing explicit Fabric map props, before that setter. Device causality remains subject to installing the new binary.

## Design and implementation batches

Alternative: rotate the child image in JS or remount per heading. Rejected because it adds bitmap snapshots/flicker and changes working rendering. Keep existing heading math and native position animation; repair native property transport instead.

TodoWrite is unavailable; this is the tracker. Each batch is at most three tracked files and committed before the next:

- [x] Driver maps patch + this log: schema, Android Java/C++ generated props, Android-only JS forwarding.
- [x] Rider sibling maps patch + this log: identical seven-file native addition; driver commit `b3d856443`.
- [x] Two app configs + this log: driver Android runtime 2.8.0, rider Android 2.2.0; inherited iOS runtimes remain 2.7.0 / 2.1.0. Rider patch commit `30e4ae894`.
- [ ] Static regression guard + this log: check the full property chain and patch parity.

## Fix, before / after, and blast radius

Before: generic rotation and undeclared flat reach an incomplete Fabric schema.
After: Android rotation is forwarded as markerRotation; schema, shipped generated C++ parser/diff, Java delegate/interface, and manager explicitly deliver it to MapMarker.setRotation. Flat has the same complete chain. Marker options use markerRotation at creation, and dynamic setters handle subsequent turns.

Both existing patch-package files retain their MapView detach/reattach patch unchanged. The dependency ships generated native code, so updating only TypeScript or Java would be insufficient. iOS JS forwarding and iOS native sources remain unchanged. No trip recording, GPS filters, road matching, billing, backend, auth, or user data changes. No new logs or location collection.

This repairs all Android markers using the public rotation/flat API, not just one trip phase. Default rotation remains zero and default flat remains false. It does not claim to fix independent GPS drift, delayed playback, or every road-snapping issue listed in the earlier change log.

## Delivery, rollback, and verification

Static evidence: added native hunks pass git apply --check against installed 1.27.2; the RN codegen TypeScript parser accepts Float markerRotation/default 0 and Boolean flat/default false. Senior native reviewer inspected all seven patched files and found no blockers. Causality caveat: missing rotation in C++ getDiffProps causes loss when RN's Android prop-reconciliation path is enabled; raw-props transport also exists. The explicit property works across both paths, but installed-device runtime flags were not observed. No claim of on-device success is made.

A NEW ANDROID APK/AAB IS REQUIRED. OTA alone cannot install these native schema/setter changes. Android runtime versions are fenced; iOS runtime versions stay unchanged. No build, deployment, publication, push or merge is performed. No remote flag can repair native transport; rollback requires reinstalling/releasing the preceding binary, not just reverting JS.

User requested coding only and owns physical testing. No native build or road tests run. No active native visual regression suite exists. Static checks and actual-diff review results will be recorded below. Acceptance on the rebuilt driver APK: N/E/S/W travel, turns, course-up/north-up, idle/pickup/in_progress, stop/start, and head-unit reconnect; verify iOS remains unchanged. A rotated map must not leave the car driving sideways.
