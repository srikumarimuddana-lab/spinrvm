"""Run actual patched MapView lifecycle methods on a JVM (no Android SDK needed).

Usage: python scripts/test_maps_lifecycle.py PATH/TO/MapView.java
Requires a JDK on PATH. Android/Google rendering is stubbed; this checks list
ownership and queued callback ordering, NOT native rendering or device safety.
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path


def method(source, name):
    match = re.search(
        rf"    (?:public|protected|private) (?:synchronized )?\w+ {name}\([^\n]*\)\s*\{{",
        source,
    )
    if not match:
        raise ValueError(f"MapView method missing: {name}")
    opening = source.index("{", match.start())
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


HARNESS = r"""
import java.util.*;
import java.util.function.Consumer;

class View {}
class MapFeature extends View {}
class Bundle {}
class Log {
    static void e(String tag, String message) { throw new AssertionError(message); }
    static void e(String tag, String message, Exception e) { throw new AssertionError(message, e); }
}
class NativeMap {
    protected void onAttachedToWindow() {}
    protected void onDetachedFromWindow() {}
    void onCreate(Bundle state) {}
    void onStart() {}
    void onResume() {}
    void onPause() {}
    void onStop() {}
    void onDestroy() {}
    void onSaveInstanceState(Bundle state) {}
    void removeView(Object view) {}
}
public class MapLifecycleHarness extends NativeMap {
    /*FIELDS*/
    boolean paused = false, destroyed = false, isMapReady = true;
    boolean shouldRestorePadding;
    Object map = new Object(), attacherGroup = new Object();
    final List<Consumer<Object>> callbacks = new ArrayList<>();
    final List<MapFeature> drawn = new ArrayList<>();
    int readyCalls;
    void attachLifecycleObserver() {}
    void detachLifecycleObserver() {}
    void prepareAttacherView() { attacherGroup = new Object(); }
    void getMapAsync(Consumer<Object> callback) { callbacks.add(callback); }
    void onMapReady(Object value) {
        if (destroyed) return; // Same early-return contract as the real method.
        map = value;
        readyCalls++;
    }
    // Only the Google/Android draw boundary is modeled. List insertion below
    // runs the library's actual safeAddFeature method, extracted unchanged.
    void addFeature(MapFeature feature, int index) {
        if (savedFeatures == null) drawn.add(feature);
        safeAddFeature(index, feature);
    }
    /*METHODS*/
    static void check(boolean result, String message) {
        if (!result) throw new AssertionError(message);
    }
    static MapLifecycleHarness seeded() {
        MapLifecycleHarness view = new MapLifecycleHarness();
        for (int i = 0; i < 4; i++) view.addFeature(new MapFeature(), i);
        view.drawn.clear();
        return view;
    }
    static void normalRestore() {
        MapLifecycleHarness view = seeded();
        View first = view.getFeatureAt(0);
        view.onDetachedFromWindow();
        check(view.getFeatureCount() == 4, "React must still see detached children");
        MapFeature added = new MapFeature();
        view.addFeature(added, 1);
        check(view.getFeatureCount() == 5, "mid-list insert must shift, not overwrite");
        check(view.getFeatureAt(0) == first && view.getFeatureAt(1) == added, "child identity");
        check(view.drawn.isEmpty(), "detached insertion must not draw");
        view.onAttachedToWindow();
        view.callbacks.get(0).accept(new Object());
        check(view.getFeatureCount() == 5, "restore must keep all children");
        check(view.drawn.size() == 5, "restore must draw each child once");
        check(view.savedFeatures == null, "ownership returns to attached list");
    }
    static void emptyRestore() {
        MapLifecycleHarness view = new MapLifecycleHarness();
        view.onDetachedFromWindow();
        view.onAttachedToWindow();
        view.callbacks.get(0).accept(new Object());
        check(view.savedFeatures == null, "empty restore must release saved list");
        view.addFeature(new MapFeature(), 0);
        check(view.drawn.size() == 1, "new route after idle must draw");
    }
    static void interruptedRestore() {
        MapLifecycleHarness view = seeded();
        view.onDetachedFromWindow();
        view.onAttachedToWindow(); // queues restore; Google Maps has not answered
        MapFeature added = new MapFeature();
        view.addFeature(added, 1);
        view.onDetachedFromWindow();
        check(view.getFeatureCount() == 5, "second detach erased saved route children");
        check(view.getFeatureAt(1) == added, "pending React mutation was lost");
        view.onAttachedToWindow();
        view.callbacks.get(1).accept(new Object());
        check(view.drawn.size() == 5, "latest attachment must restore the full route");
    }
    static void staleCallback() {
        for (boolean reattach : new boolean[]{false, true}) {
            MapLifecycleHarness view = seeded();
            view.onDetachedFromWindow();
            view.onAttachedToWindow();
            view.onDetachedFromWindow();
            if (reattach) view.onAttachedToWindow();
            view.callbacks.get(0).accept(new Object());
            check(view.readyCalls == 0, "obsolete callback reinitialized the map");
            check(view.drawn.isEmpty(), "obsolete callback drew a detached route");
            check(view.getFeatureCount() == 4, "obsolete callback lost children");
            if (reattach) {
                view.callbacks.get(1).accept(new Object());
                check(view.drawn.size() == 4, "current callback must still restore");
                view.callbacks.get(0).accept(new Object());
                check(view.readyCalls == 1, "late obsolete callback replaced current map");
            }
        }
    }
    static void coldDetach() {
        MapLifecycleHarness view = seeded();
        view.map = null;
        view.isMapReady = false;
        view.onDetachedFromWindow();
        check(view.savedMapState == null, "cold map has no saved Google state");
        view.onAttachedToWindow();
        check(view.callbacks.size() == 1, "cold detach never schedules child restore");
        view.callbacks.get(0).accept(new Object());
        check(view.drawn.size() == 4, "cold attachment must restore route children");
    }
    static void destroyedCallback() {
        MapLifecycleHarness view = seeded();
        view.onDetachedFromWindow();
        view.onAttachedToWindow();
        view.doDestroy();
        view.callbacks.get(0).accept(new Object());
        check(view.readyCalls == 0, "restore callback ran after destroy");
    }
    public static void main(String[] args) {
        Runnable[] cases = {MapLifecycleHarness::normalRestore, MapLifecycleHarness::emptyRestore,
            MapLifecycleHarness::interruptedRestore, MapLifecycleHarness::staleCallback,
            MapLifecycleHarness::coldDetach, MapLifecycleHarness::destroyedCallback};
        int failed = 0;
        for (Runnable test : cases) {
            try { test.run(); } catch (AssertionError e) {
                failed++;
                System.err.println("FAIL: " + e.getMessage());
            }
        }
        if (failed > 0) throw new AssertionError(failed + " lifecycle cases failed");
        System.out.println("PASS: " + cases.length + " MapView lifecycle cases");
    }
}
"""


def main():
    source = Path(sys.argv[1]).read_text(encoding="utf-8")
    # Extract the real fields as well, so later lifecycle guards cannot be
    # accidentally supplied by the test instead of the production patch.
    fields = []
    for line in source.splitlines():
        if re.match(r"    private .* (savedMapState|savedFeatures|features|featureRestoreGeneration)\b", line):
            fields.append(line)
    methods = [method(source, name) for name in (
        "onAttachedToWindow", "onDetachedFromWindow", "safeAddFeature",
        "getFeatureCount", "getFeatureAt", "doDestroy",
    )]
    java = HARNESS.replace("/*FIELDS*/", "\n".join(fields)).replace("/*METHODS*/", "\n".join(methods))
    with tempfile.TemporaryDirectory(prefix="spinr-maps-test-") as temp:
        path = Path(temp) / "MapLifecycleHarness.java"
        path.write_text(java, encoding="utf-8")
        subprocess.run(["javac", str(path)], check=True)
        subprocess.run(["java", "-cp", temp, "MapLifecycleHarness"], check=True)


if __name__ == "__main__":
    main()
