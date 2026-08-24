import { z } from "zod";

import type { TimeWindowPolicy } from "@/lib/api/corporate";

/**
 * Pure extraction of the inline validation duplicated across
 * `corporate-accounts/[id]/policy/page.tsx`'s `TimeWindowRow` (per-row
 * `invalid` flag) and its parent's `hasInvalidWindow` (save-button guard):
 * both computed `w.end <= w.start` by hand. This schema is the single
 * source of truth for that one rule — same accept/reject behavior, same
 * error message ("End must be after start"), nothing added or removed.
 */
export const timeWindowSchema = z
    .object({
        day: z.enum(["mon", "tue", "wed", "thu", "fri", "sat", "sun"]),
        start: z.string(),
        end: z.string(),
    })
    .superRefine((val, ctx) => {
        if (val.end <= val.start) {
            ctx.addIssue({
                code: z.ZodIssueCode.custom,
                message: "End must be after start",
                path: ["end"],
            });
        }
    });

export function isTimeWindowValid(window: TimeWindowPolicy): boolean {
    return timeWindowSchema.safeParse(window).success;
}

export function hasInvalidTimeWindow(windows: TimeWindowPolicy[]): boolean {
    return windows.some((w) => !isTimeWindowValid(w));
}
