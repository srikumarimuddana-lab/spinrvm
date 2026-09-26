/**
 * UX program W2.1: closing a payout period writes a permanent audit row, so
 * it must only happen after the in-app confirmation's "Close period" button.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { PayoutsOverview } from "@/lib/api";

const toast = vi.fn();
const closePayoutPeriod = vi.fn();

vi.mock("@/components/ui/use-toast", () => ({ useToast: () => ({ toast }) }));
vi.mock("@/lib/api", () => ({
    closePayoutPeriod: (year: number, month: number) => closePayoutPeriod(year, month),
}));

import { PayoutsCompliance } from "./payouts-compliance";

const overview = {
    t4a_snapshot: {
        tax_year: 2026,
        drivers_with_earnings: 0,
        buckets: { under_500: 0, from_500_to_10k: 0, from_10k_to_30k: 0, over_30k: 0 },
        ytd_gross_earnings: 0,
    },
    period_locks: [],
} as unknown as PayoutsOverview;

async function clickClose() {
    const user = userEvent.setup();
    const onClosed = vi.fn();
    render(<PayoutsCompliance overview={overview} onClosed={onClosed} />);
    await user.click(screen.getByRole("button", { name: /^Close / }));
    await screen.findByRole("alertdialog");
    return { user, onClosed };
}

describe("PayoutsCompliance period close", () => {
    beforeEach(() => {
        toast.mockClear();
        closePayoutPeriod.mockReset().mockResolvedValue({ period: "2026-08", payout_count: 3, total_amount: 120 });
    });

    it("does not close the period on Cancel", async () => {
        const { user, onClosed } = await clickClose();
        await user.click(screen.getByRole("button", { name: "Cancel" }));
        await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
        expect(closePayoutPeriod).not.toHaveBeenCalled();
        expect(onClosed).not.toHaveBeenCalled();
    });

    it("closes the period on confirm", async () => {
        const { user, onClosed } = await clickClose();
        await user.click(screen.getByRole("button", { name: "Close period" }));
        await waitFor(() => expect(closePayoutPeriod).toHaveBeenCalledTimes(1));
        await waitFor(() => expect(onClosed).toHaveBeenCalledTimes(1));
    });
});
