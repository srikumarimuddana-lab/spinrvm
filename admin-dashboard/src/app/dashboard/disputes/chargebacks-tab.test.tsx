/**
 * ChargebacksTab — C23 items 3-5: read-only chargebacks list, evidence-pack
 * download (item 4), and the super_admin-only Stripe submission flow (item 5).
 */

import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const getChargebacks = vi.fn();
const downloadDisputeEvidencePack = vi.fn();
const submitDisputeEvidence = vi.fn();

vi.mock("@/lib/api", () => ({
  getChargebacks: (...a: unknown[]) => getChargebacks(...a),
  downloadDisputeEvidencePack: (...a: unknown[]) => downloadDisputeEvidencePack(...a),
  submitDisputeEvidence: (...a: unknown[]) => submitDisputeEvidence(...a),
}));

let mockRole = "support_admin";
vi.mock("@/store/authStore", () => ({
  useAuthStore: (selector: (s: { user: { role: string } }) => unknown) =>
    selector({ user: { role: mockRole } }),
}));

vi.mock("@/components/ui/dialog", () => ({
  Dialog: ({ children, open }: React.PropsWithChildren<{ open?: boolean }>) =>
    open ? <div>{children}</div> : null,
  DialogContent: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  DialogHeader: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  DialogTitle: ({ children }: React.PropsWithChildren) => <h2>{children}</h2>,
}));

vi.mock("lucide-react", () => ({
  AlertTriangle: () => <span />,
  RefreshCw: () => <span />,
  Download: () => <span />,
  Send: () => <span />,
  // Pulled in indirectly by the real (unstubbed) SortableHead/Pagination.
  ChevronUp: () => <span />,
  ChevronDown: () => <span />,
  ChevronsUpDown: () => <span />,
  ChevronLeft: () => <span />,
  ChevronRight: () => <span />,
}));

import ChargebacksTab from "./chargebacks-tab";

const OPEN_CHARGEBACK = {
  id: "sd-1",
  stripe_dispute_id: "dp_1",
  ride_id: "ride-1",
  ride_code: "SPN-1",
  amount_cents: 5000,
  reason: "fraudulent",
  status: "needs_response",
  evidence_due_by: "2026-08-25T00:00:00+00:00",
  evidence_submitted_at: null,
  days_remaining: 3,
  created_at: "2026-08-18T00:00:00+00:00",
  updated_at: "2026-08-18T00:00:00+00:00",
};

const SUBMITTED_CHARGEBACK = {
  ...OPEN_CHARGEBACK,
  id: "sd-2",
  ride_code: "SPN-2",
  evidence_submitted_at: "2026-08-18T00:00:00+00:00",
};

beforeEach(() => {
  vi.clearAllMocks();
  mockRole = "support_admin";
  getChargebacks.mockResolvedValue([OPEN_CHARGEBACK]);
  downloadDisputeEvidencePack.mockResolvedValue({ blob: new Blob(["x"]), filename: "dispute.zip" });
  submitDisputeEvidence.mockResolvedValue({ submitted: true, stripe_dispute_id: "dp_1", dispute_id: "sd-1" });
  global.URL.createObjectURL = vi.fn(() => "blob:mock");
  global.URL.revokeObjectURL = vi.fn();
});

describe("ChargebacksTab", () => {
  it("lists chargebacks from the API", async () => {
    render(<ChargebacksTab />);
    expect(await screen.findByText("SPN-1")).toBeInTheDocument();
    expect(getChargebacks).toHaveBeenCalled();
  });

  it("downloads the evidence pack for a row", async () => {
    render(<ChargebacksTab />);
    await screen.findByText("SPN-1");
    fireEvent.click(screen.getByRole("button", { name: /Download evidence pack for SPN-1/ }));
    await waitFor(() =>
      expect(downloadDisputeEvidencePack).toHaveBeenCalledWith("ride-1", "SPN-1"),
    );
  });

  it("surfaces a download error without crashing", async () => {
    downloadDisputeEvidencePack.mockRejectedValue(new Error("network down"));
    render(<ChargebacksTab />);
    await screen.findByText("SPN-1");
    fireEvent.click(screen.getByRole("button", { name: /Download evidence pack for SPN-1/ }));
    expect(await screen.findByText("network down")).toBeInTheDocument();
  });

  it("announces the download-failure banner to screen readers (role=alert)", async () => {
    // accessibility-reviewer finding: this deadline-monitoring surface must
    // announce a failed download, not just render it silently for sighted
    // users only.
    downloadDisputeEvidencePack.mockRejectedValue(new Error("network down"));
    render(<ChargebacksTab />);
    await screen.findByText("SPN-1");
    fireEvent.click(screen.getByRole("button", { name: /Download evidence pack for SPN-1/ }));
    const alertEl = await screen.findByRole("alert");
    expect(alertEl).toHaveTextContent("network down");
  });

  it("announces the list-fetch-failure banner to screen readers (role=alert)", async () => {
    getChargebacks.mockRejectedValue(new Error("db down"));
    render(<ChargebacksTab />);
    const alertEl = await screen.findByRole("alert");
    expect(alertEl).toHaveTextContent("Failed to load chargebacks");
  });

  it("hides the Submit-to-Stripe action for a non-super_admin", async () => {
    mockRole = "support_admin";
    render(<ChargebacksTab />);
    await screen.findByText("SPN-1");
    expect(screen.queryByRole("button", { name: /Submit evidence to Stripe/ })).not.toBeInTheDocument();
  });

  it("shows the Submit-to-Stripe action for a super_admin and submits via the dialog", async () => {
    mockRole = "super_admin";
    render(<ChargebacksTab />);
    await screen.findByText("SPN-1");

    fireEvent.click(screen.getByRole("button", { name: /Submit evidence to Stripe for SPN-1/ }));
    expect(await screen.findByText(/cannot be undone/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Submit to Stripe" }));
    await waitFor(() => expect(submitDisputeEvidence).toHaveBeenCalledWith("sd-1", undefined));
  });

  it("shows a Submitted badge instead of an action when already submitted", async () => {
    mockRole = "super_admin";
    getChargebacks.mockResolvedValue([SUBMITTED_CHARGEBACK]);
    render(<ChargebacksTab />);
    await screen.findByText("SPN-2");
    expect(screen.getByText("Submitted")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Submit evidence to Stripe/ })).not.toBeInTheDocument();
  });

  it("surfaces a submission error inside the dialog without closing it", async () => {
    mockRole = "super_admin";
    submitDisputeEvidence.mockRejectedValue(new Error("Stripe rejected the evidence submission"));
    render(<ChargebacksTab />);
    await screen.findByText("SPN-1");
    fireEvent.click(screen.getByRole("button", { name: /Submit evidence to Stripe for SPN-1/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Submit to Stripe" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Stripe rejected the evidence submission");
  });
});
