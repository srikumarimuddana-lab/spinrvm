import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setVisibleInterval } from "../visible-interval";

let hidden = false;

function setHidden(next: boolean) {
    hidden = next;
    document.dispatchEvent(new Event("visibilitychange"));
}

beforeEach(() => {
    vi.useFakeTimers();
    hidden = false;
    Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
});

afterEach(() => {
    vi.useRealTimers();
    // Restore jsdom's own getter (on Document.prototype).
    delete (document as { hidden?: boolean }).hidden;
});

describe("setVisibleInterval", () => {
    it("does not run immediately, then runs every interval while visible", () => {
        const cb = vi.fn();
        const stop = setVisibleInterval(cb, 1_000);
        expect(cb).not.toHaveBeenCalled();
        vi.advanceTimersByTime(1_000);
        expect(cb).toHaveBeenCalledTimes(1);
        vi.advanceTimersByTime(3_000);
        expect(cb).toHaveBeenCalledTimes(4);
        stop();
    });

    it("stops running while the tab is hidden", () => {
        const cb = vi.fn();
        const stop = setVisibleInterval(cb, 1_000);
        vi.advanceTimersByTime(1_000);
        expect(cb).toHaveBeenCalledTimes(1);

        setHidden(true);
        vi.advanceTimersByTime(60_000);
        expect(cb).toHaveBeenCalledTimes(1);
        stop();
    });

    it("runs at once on return when a full interval has passed, then resumes the cadence", () => {
        const cb = vi.fn();
        const stop = setVisibleInterval(cb, 1_000);
        setHidden(true);
        vi.advanceTimersByTime(5_000);
        expect(cb).not.toHaveBeenCalled();

        setHidden(false);
        expect(cb).toHaveBeenCalledTimes(1);
        vi.advanceTimersByTime(1_000);
        expect(cb).toHaveBeenCalledTimes(2);
        stop();
    });

    it("waits out the rest of the interval on a quick tab switch instead of running extra", () => {
        const cb = vi.fn();
        const stop = setVisibleInterval(cb, 10_000);
        vi.advanceTimersByTime(4_000);
        setHidden(true);
        vi.advanceTimersByTime(2_000);
        setHidden(false); // 6 s since the start: 4 s left
        expect(cb).not.toHaveBeenCalled();
        vi.advanceTimersByTime(3_999);
        expect(cb).not.toHaveBeenCalled();
        vi.advanceTimersByTime(1);
        expect(cb).toHaveBeenCalledTimes(1);
        stop();
    });

    it("schedules nothing when started in a hidden tab, and catches up once shown", () => {
        hidden = true;
        const cb = vi.fn();
        const stop = setVisibleInterval(cb, 1_000);
        vi.advanceTimersByTime(10_000);
        expect(cb).not.toHaveBeenCalled();
        setHidden(false);
        expect(cb).toHaveBeenCalledTimes(1);
        stop();
    });

    it("keeps polling after the callback throws", () => {
        const cb = vi.fn(() => {
            throw new Error("boom");
        });
        const stop = setVisibleInterval(cb, 1_000);
        expect(() => vi.advanceTimersByTime(1_000)).toThrow("boom");
        expect(() => vi.advanceTimersByTime(1_000)).toThrow("boom");
        expect(cb).toHaveBeenCalledTimes(2);
        stop();
    });

    it("keeps polling when the catch-up run on return throws", () => {
        const cb = vi.fn(() => {
            throw new Error("boom");
        });
        const stop = setVisibleInterval(cb, 1_000);
        setHidden(true);
        vi.advanceTimersByTime(5_000);
        // The catch-up run throws inside the visibilitychange listener; the
        // DOM reports a listener's error rather than rethrowing it here.
        const onError = (e: ErrorEvent) => e.preventDefault();
        window.addEventListener("error", onError);
        setHidden(false);
        window.removeEventListener("error", onError);
        expect(cb).toHaveBeenCalledTimes(1);
        expect(() => vi.advanceTimersByTime(1_000)).toThrow("boom");
        expect(cb).toHaveBeenCalledTimes(2);
        stop();
    });

    it("keeps polling when the delayed run after a quick return throws", () => {
        const cb = vi.fn(() => {
            throw new Error("boom");
        });
        const stop = setVisibleInterval(cb, 10_000);
        vi.advanceTimersByTime(4_000);
        setHidden(true);
        setHidden(false); // 6 s left
        expect(() => vi.advanceTimersByTime(6_000)).toThrow("boom");
        expect(() => vi.advanceTimersByTime(10_000)).toThrow("boom");
        expect(cb).toHaveBeenCalledTimes(2);
        stop();
    });

    it("stop() cancels the next run and ignores later visibility changes", () => {
        const cb = vi.fn();
        const stop = setVisibleInterval(cb, 1_000);
        stop();
        vi.advanceTimersByTime(5_000);
        setHidden(true);
        setHidden(false);
        vi.advanceTimersByTime(5_000);
        expect(cb).not.toHaveBeenCalled();
    });

    it("does not double-schedule when shown twice without being hidden", () => {
        const cb = vi.fn();
        const stop = setVisibleInterval(cb, 1_000);
        setHidden(false);
        setHidden(false);
        vi.advanceTimersByTime(1_000);
        expect(cb).toHaveBeenCalledTimes(1);
        stop();
    });
});
