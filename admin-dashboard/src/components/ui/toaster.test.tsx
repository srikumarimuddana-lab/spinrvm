/**
 * UX program W2.2 toast policy: up to 3 toasts at once; errors stay until
 * dismissed and are announced assertively; other toasts auto-dismiss and are
 * announced politely; a caller's own duration still wins.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen } from "@testing-library/react";

let Toaster: typeof import("./toaster").Toaster;
let toast: typeof import("./use-toast").toast;

beforeEach(async () => {
    // use-toast keeps its queue in module state; load a fresh copy per test.
    vi.resetModules();
    ({ Toaster } = await import("./toaster"));
    ({ toast } = await import("./use-toast"));
    vi.useFakeTimers();
});

afterEach(() => {
    vi.useRealTimers();
});

function show(opts: Parameters<typeof toast>[0]) {
    act(() => { toast(opts); });
}

function advance(ms: number) {
    act(() => { vi.advanceTimersByTime(ms); });
}

describe("Toaster policy", () => {
    it("keeps an error toast until it is dismissed", () => {
        render(<Toaster />);
        show({ title: "Save failed", variant: "destructive" });
        advance(60_000);
        expect(screen.getByText("Save failed")).toBeInTheDocument();
    });

    it("auto-dismisses a non-error toast", () => {
        render(<Toaster />);
        show({ title: "Saved" });
        expect(screen.getByText("Saved")).toBeInTheDocument();
        advance(10_000);
        expect(screen.queryByText("Saved")).toBeNull();
    });

    it("lets a caller's own duration win", () => {
        render(<Toaster />);
        show({ title: "Copy failed", variant: "destructive", duration: 1500 });
        advance(3_000);
        expect(screen.queryByText("Copy failed")).toBeNull();
    });

    it("shows up to 3 toasts, dropping the oldest", () => {
        render(<Toaster />);
        ["One", "Two", "Three", "Four"].forEach((title) => show({ title, variant: "destructive" }));
        expect(screen.queryByText("One")).toBeNull();
        ["Two", "Three", "Four"].forEach((title) => expect(screen.getByText(title)).toBeInTheDocument());
    });

    it("announces errors assertively and other toasts politely", () => {
        render(<Toaster />);
        show({ title: "Save failed", variant: "destructive" });
        show({ title: "Saved" });
        // Radix fills its screen-reader announcement on the next frame.
        advance(50);
        const regions = Array.from(document.querySelectorAll("[aria-live]"));
        const assertive = regions.find((el) => el.getAttribute("aria-live") === "assertive");
        const polite = regions.find((el) => el.getAttribute("aria-live") === "polite" && el.textContent?.includes("Saved"));
        expect(assertive?.textContent).toContain("Save failed");
        expect(polite).toBeDefined();
    });

    it("gives the close button a name and shows it on error toasts", () => {
        render(<Toaster />);
        show({ title: "Save failed", variant: "destructive" });
        const close = screen.getByRole("button", { name: "Dismiss notification" });
        expect(close).toHaveClass("group-[.destructive]:opacity-100", "group-[.destructive]:text-destructive-foreground");
        // 70% white on red is 2.98:1, under SC 1.4.11's 3:1 for an always-visible icon.
        expect(close.className).not.toContain("text-destructive-foreground/70");
        act(() => { close.click(); });
        advance(1_000);
        expect(screen.queryByText("Save failed")).toBeNull();
    });
});
