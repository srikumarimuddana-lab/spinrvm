/**
 * A `setInterval` that pauses while the browser tab is hidden (UX program
 * W5.3).
 *
 * Admin pages poll the backend every 5–60 s. Browsers only throttle timers
 * in a background tab; they don't stop them, so a dashboard left open in a
 * background tab kept polling all day for a screen no one was looking at.
 *
 * `setVisibleInterval(callback, intervalMs)` is a drop-in for
 * `setInterval(callback, intervalMs)` that returns its own stop function:
 *
 *   - While the page is visible, `callback` runs every `intervalMs`.
 *   - When the page becomes hidden, the pending run is cancelled.
 *   - When it becomes visible again, `callback` runs at once if a full
 *     interval has passed since the last run, so the data catches up
 *     straight away. Otherwise the next run is scheduled for the rest of
 *     that interval, so quick tab switches don't trigger extra requests.
 *
 * Like `setInterval`, it does not run `callback` immediately. The caller's
 * own first load counts as the last run. Outside a browser it falls back to
 * a plain `setInterval`.
 *
 * Nothing here decides *whether* a page should poll. Pages still own their
 * fetch, their error handling and their WebSocket-vs-poll logic.
 */
export function setVisibleInterval(callback: () => void, intervalMs: number): () => void {
    if (typeof document === "undefined") {
        const id = setInterval(callback, intervalMs);
        return () => clearInterval(id);
    }

    // While visible this is a plain setInterval. setTimeout is only used for
    // the partial wait after the tab comes back before a full interval passed.
    let intervalId: ReturnType<typeof setInterval> | null = null;
    let resumeId: ReturnType<typeof setTimeout> | null = null;
    let lastRun = Date.now();
    let stopped = false;

    const run = () => {
        lastRun = Date.now();
        callback();
    };

    const startInterval = () => {
        if (stopped || document.hidden || intervalId != null) return;
        intervalId = setInterval(run, intervalMs);
    };

    const clearTimers = () => {
        if (intervalId != null) clearInterval(intervalId);
        if (resumeId != null) clearTimeout(resumeId);
        intervalId = null;
        resumeId = null;
    };

    const onVisibilityChange = () => {
        if (stopped) return;
        if (document.hidden) {
            clearTimers();
            return;
        }
        if (intervalId != null || resumeId != null) return; // already running
        const dueInMs = intervalMs - (Date.now() - lastRun);
        if (dueInMs <= 0) {
            // Start the interval before calling, so a callback that throws
            // can't stop the polling (setInterval keeps going after a throw).
            startInterval();
            run();
        } else {
            resumeId = setTimeout(() => {
                resumeId = null;
                startInterval();
                run();
            }, dueInMs);
        }
    };

    document.addEventListener("visibilitychange", onVisibilityChange);
    startInterval();

    return () => {
        stopped = true;
        clearTimers();
        document.removeEventListener("visibilitychange", onVisibilityChange);
    };
}
