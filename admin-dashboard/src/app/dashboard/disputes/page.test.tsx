/**
 * In-app disputes are disabled (2026-09-25): /dashboard/disputes shows only
 * card-network chargebacks. No "Rider Disputes" tab, and the page never
 * loads or resolves rider disputes.
 */

import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

const getChargebacks = vi.fn();
const getDisputes = vi.fn();
const getDisputeStats = vi.fn();
const resolveDispute = vi.fn();

vi.mock("@/lib/api", () => ({
  getChargebacks: (...a: unknown[]) => getChargebacks(...a),
  downloadDisputeEvidencePack: vi.fn(),
  submitDisputeEvidence: vi.fn(),
  getDisputes: (...a: unknown[]) => getDisputes(...a),
  getDisputeStats: (...a: unknown[]) => getDisputeStats(...a),
  resolveDispute: (...a: unknown[]) => resolveDispute(...a),
}));
vi.mock("@/hooks/useRequireModule", () => ({ useRequireModule: () => ({ allowed: true }) }));
vi.mock("@/store/authStore", () => ({
  useAuthStore: (selector: (s: { user: { role: string } }) => unknown) =>
    selector({ user: { role: "support_admin" } }),
}));

import DisputesPage from "./page";

describe("DisputesPage — chargebacks only", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getChargebacks.mockResolvedValue([
      {
        id: "cb1",
        stripe_dispute_id: "dp_test_1",
        ride_id: "ride-1",
        ride_code: "RIDE-1",
        amount_cents: 2500,
        reason: "fraudulent",
        status: "needs_response",
        evidence_due_by: null,
        evidence_submitted_at: null,
        days_remaining: null,
        created_at: "2026-09-25T00:00:00+00:00",
        updated_at: "2026-09-25T00:00:00+00:00",
      },
    ]);
  });

  it("renders the chargebacks list directly, with no Rider Disputes tab", async () => {
    render(<DisputesPage />);
    expect(await screen.findByText("RIDE-1")).toBeTruthy();
    expect(getChargebacks).toHaveBeenCalled();
    expect(screen.queryByText("Rider Disputes")).toBeNull();
    expect(screen.queryByRole("tab")).toBeNull();
    expect(screen.queryByRole("button", { name: "Resolve" })).toBeNull();
  });

  it("never loads or resolves in-app disputes", async () => {
    render(<DisputesPage />);
    await screen.findByText("RIDE-1");
    expect(getDisputes).not.toHaveBeenCalled();
    expect(getDisputeStats).not.toHaveBeenCalled();
    expect(resolveDispute).not.toHaveBeenCalled();
  });

  it("points admins at the support address instead of an in-app flow", () => {
    render(<DisputesPage />);
    expect(screen.getByText(/support@spinr\.ca/)).toBeTruthy();
  });
});
