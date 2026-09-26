/**
 * UX program W2.1b: cancelling a company booking cancels a real ride and
 * texts the customer, so it must only happen on the in-app confirmation's
 * "Cancel booking" button; "Keep booking" leaves the ride alone.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const cancelCompanyBooking = vi.fn();
const listCompanyBookings = vi.fn();

vi.mock("next/navigation", () => ({ useParams: () => ({ id: "co-1" }) }));
vi.mock("@/store/companyAuthStore", () => ({
    useCompanyAuthStore: (selector: (s: { memberships: unknown[] }) => unknown) => selector({ memberships: [] }),
}));
vi.mock("@/lib/companyApi", () => ({
    cancelCompanyBooking: (companyId: string, rideId: string) => cancelCompanyBooking(companyId, rideId),
    listCompanyBookings: (...args: unknown[]) => listCompanyBookings(...args),
}));

import CompanyBookingsPage from "./page";

async function openCancelDialog() {
    const user = userEvent.setup();
    render(<CompanyBookingsPage />);
    await user.click(await screen.findByRole("button", { name: "Cancel" }));
    await screen.findByRole("alertdialog");
    return user;
}

describe("Company bookings cancel", () => {
    beforeEach(() => {
        cancelCompanyBooking.mockReset().mockResolvedValue(undefined);
        listCompanyBookings.mockReset().mockResolvedValue({
            bookings: [{ ride_id: "ride-1", status: "searching", guest_booking: true, customer_first_name: "Sam" }],
        });
    });

    it("keeps the booking on Keep booking", async () => {
        const user = await openCancelDialog();
        await user.click(screen.getByRole("button", { name: "Keep booking" }));
        await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
        expect(cancelCompanyBooking).not.toHaveBeenCalled();
    });

    it("cancels the booking on Cancel booking", async () => {
        const user = await openCancelDialog();
        await user.click(screen.getByRole("button", { name: "Cancel booking" }));
        await waitFor(() => expect(cancelCompanyBooking).toHaveBeenCalledWith("co-1", "ride-1"));
    });
});
