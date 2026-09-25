/**
 * N23: resolving a dispute when the backend issued no refund (the
 * admin_dispute_refunds_enabled flag is off, or the Stripe refund failed) must
 * tell the admin, not close the dialog as if the refund went through.
 */

import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const getDisputes = vi.fn();
const getDisputeStats = vi.fn();
const resolveDispute = vi.fn();
const toast = vi.fn();

vi.mock("@/lib/api", () => ({
  getDisputes: (...a: unknown[]) => getDisputes(...a),
  getDisputeStats: (...a: unknown[]) => getDisputeStats(...a),
  resolveDispute: (...a: unknown[]) => resolveDispute(...a),
}));
vi.mock("@/hooks/useRequireModule", () => ({ useRequireModule: () => ({ allowed: true }) }));
vi.mock("@/components/ui/use-toast", () => ({ useToast: () => ({ toast }) }));
vi.mock("./chargebacks-tab", () => ({ default: () => null }));
vi.mock("@/components/ui/dialog", () => ({
  Dialog: ({ children, open }: React.PropsWithChildren<{ open?: boolean }>) =>
    open ? <div>{children}</div> : null,
  DialogContent: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  DialogHeader: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  DialogTitle: ({ children }: React.PropsWithChildren) => <h2>{children}</h2>,
}));

import DisputesPage from "./page";

const OPEN_DISPUTE = {
  id: "d1",
  user_name: "Rider One",
  reason: "overcharged",
  original_fare: 20,
  requested_amount: 20,
  status: "open",
  description: "Charged twice",
  created_at: "2026-09-25T00:00:00+00:00",
};

async function resolveOpenDispute() {
  render(<DisputesPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Resolve" }));
  fireEvent.click(screen.getByRole("button", { name: "Submit Resolution" }));
}

describe("DisputesPage resolve — refund_issued notice", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getDisputes.mockResolvedValue([OPEN_DISPUTE]);
    getDisputeStats.mockResolvedValue(null);
  });

  it("shows the backend message when no refund was issued", async () => {
    resolveDispute.mockResolvedValue({
      success: true,
      resolution: "approved",
      refund_issued: false,
      message: "Dispute resolved, but no refund was issued. Issue the refund manually in Stripe.",
    });
    await resolveOpenDispute();
    await waitFor(() => expect(toast).toHaveBeenCalledTimes(1));
    expect(toast.mock.calls[0][0]).toMatchObject({
      title: "Dispute resolved, no refund issued",
      description: "Dispute resolved, but no refund was issued. Issue the refund manually in Stripe.",
    });
  });

  it("stays quiet when the refund was issued", async () => {
    resolveDispute.mockResolvedValue({ success: true, resolution: "approved", refund_issued: true });
    await resolveOpenDispute();
    await waitFor(() => expect(resolveDispute).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Submit Resolution" })).toBeNull());
    expect(toast).not.toHaveBeenCalled();
  });
});
