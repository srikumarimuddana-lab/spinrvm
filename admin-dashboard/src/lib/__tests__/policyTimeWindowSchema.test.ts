import { describe, expect, it } from "vitest";

import {
    hasInvalidTimeWindow,
    isTimeWindowValid,
    timeWindowSchema,
} from "@/lib/policyTimeWindowSchema";

function win(day: string, start: string, end: string) {
    return { day, start, end } as any;
}

describe("timeWindowSchema / isTimeWindowValid", () => {
    // Accept cases — end strictly after start, mirrors the original
    // `w.end <= w.start` check (false => valid) across every day literal.
    it("accepts a normal business-hours window", () => {
        expect(isTimeWindowValid(win("mon", "09:00", "18:00"))).toBe(true);
    });

    it("accepts an early-morning window", () => {
        expect(isTimeWindowValid(win("tue", "00:00", "00:01"))).toBe(true);
    });

    it("accepts a window one minute wide", () => {
        expect(isTimeWindowValid(win("wed", "12:00", "12:01"))).toBe(true);
    });

    it("accepts a window that ends at 23:59", () => {
        expect(isTimeWindowValid(win("thu", "00:00", "23:59"))).toBe(true);
    });

    it("accepts every remaining day literal with a valid range", () => {
        expect(isTimeWindowValid(win("fri", "08:00", "17:00"))).toBe(true);
        expect(isTimeWindowValid(win("sat", "10:00", "14:00"))).toBe(true);
        expect(isTimeWindowValid(win("sun", "06:00", "07:00"))).toBe(true);
    });

    // Reject cases — end <= start, matching the exact original predicate.
    it("rejects when end equals start", () => {
        expect(isTimeWindowValid(win("mon", "09:00", "09:00"))).toBe(false);
    });

    it("rejects when end is before start", () => {
        expect(isTimeWindowValid(win("mon", "18:00", "09:00"))).toBe(false);
    });

    it("rejects an overnight-style window (end 00:00, start 23:00)", () => {
        expect(isTimeWindowValid(win("fri", "23:00", "00:00"))).toBe(false);
    });

    it("rejects when both start and end are 00:00", () => {
        expect(isTimeWindowValid(win("sun", "00:00", "00:00"))).toBe(false);
    });

    it("rejects a one-minute-reversed window", () => {
        expect(isTimeWindowValid(win("wed", "12:01", "12:00"))).toBe(false);
    });

    it("produces the exact original error message on failure", () => {
        const result = timeWindowSchema.safeParse(win("mon", "09:00", "09:00"));
        expect(result.success).toBe(false);
        if (!result.success) {
            expect(result.error.issues[0].message).toBe("End must be after start");
        }
    });

    it("attaches the issue to the end field", () => {
        const result = timeWindowSchema.safeParse(win("mon", "10:00", "09:00"));
        expect(result.success).toBe(false);
        if (!result.success) {
            expect(result.error.issues[0].path).toEqual(["end"]);
        }
    });
});

describe("hasInvalidTimeWindow", () => {
    // Matches the original `timeWindows.some((w) => w.end <= w.start)`.
    it("returns false for an empty list", () => {
        expect(hasInvalidTimeWindow([])).toBe(false);
    });

    it("returns false when every window is valid", () => {
        expect(
            hasInvalidTimeWindow([
                win("mon", "09:00", "17:00"),
                win("tue", "08:00", "12:00"),
            ])
        ).toBe(false);
    });

    it("returns true when any single window is invalid, regardless of position", () => {
        expect(
            hasInvalidTimeWindow([
                win("mon", "09:00", "17:00"),
                win("tue", "12:00", "08:00"),
                win("wed", "08:00", "10:00"),
            ])
        ).toBe(true);
    });

    it("returns true when the only window is invalid", () => {
        expect(hasInvalidTimeWindow([win("fri", "10:00", "10:00")])).toBe(true);
    });
});
