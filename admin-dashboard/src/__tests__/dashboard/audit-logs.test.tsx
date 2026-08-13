/**
 * Audit-log page tests.
 *
 * Motivated by a real incident (2026-08-13, Zoho credential overwrite) where
 * the audit trail held the answer but the page couldn't surface it:
 *   - the Action filter offered "created"/"updated"/"deleted"/"login"/
 *     "status_change" — none of which exist in audit_logs, so every option
 *     returned an empty table while looking like it worked;
 *   - the Entity filter offered singular names ("driver", "promotion") while
 *     writers store plurals ("drivers", "promotions");
 *   - `details` (which carries fields_changed — the key evidence) was
 *     truncated to a JSON-string snippet with no way to expand it;
 *   - request_id, the join key to backend logs and Sentry, wasn't shown.
 *
 * See docs/change-log/2026-08-13-audit-log-page-investigability.md
 */

import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const getAuditLogs = vi.fn();
const getAuditLogTopActors = vi.fn();
const getAuditLogFacets = vi.fn();

vi.mock("@/lib/api", () => ({
  getAuditLogs: (...a: unknown[]) => getAuditLogs(...a),
  getAuditLogTopActors: (...a: unknown[]) => getAuditLogTopActors(...a),
  getAuditLogFacets: (...a: unknown[]) => getAuditLogFacets(...a),
}));

vi.mock("@/hooks/useRequireModule", () => ({
  useRequireModule: () => ({ allowed: true }),
}));

import AuditLogsPage, {
  humanizeAction,
  actionColor,
  formatDetails,
  detailsSummary,
} from "@/app/dashboard/audit-logs/page";

// The row that cracked the Zoho incident.
const ZOHO_LOG = {
  id: "log-1",
  created_at: "2026-08-13T19:17:30.895Z",
  action: "zoho_desk_config_updated",
  entity_type: "zoho_desk_config",
  entity_id: "default",
  actor_id: "admin-001",
  actor_role: "super_admin",
  request_id: "4e233267-f049-4344-a5ba-db952ff7bdab",
  details: JSON.stringify({
    actor_id: "admin-001",
    actor_role: "super_admin",
    fields_changed: ["client_id", "client_secret", "access_token"],
  }),
};

describe("audit-log formatting helpers", () => {
  it("humanizes the specific action verbs writers actually emit", () => {
    expect(humanizeAction("zoho_desk_config_updated")).toBe("Zoho desk config updated");
    expect(humanizeAction("otp_sent")).toBe("Otp sent");
    expect(humanizeAction(undefined)).toBe("—");
  });

  it("colours by action shape, not a fixed whitelist", () => {
    // Every one of these is a real action name from the table; under the old
    // hardcoded map all five rendered the same grey.
    const colors = [
      actionColor("pii_revealed"),
      actionColor("driver_approve"),
      actionColor("zoho_desk_config_updated"),
      actionColor("otp_sent"),
      actionColor("service_area_deleted"),
    ];
    expect(new Set(colors).size).toBeGreaterThan(1);
    colors.forEach((c) => expect(c).not.toBe("bg-zinc-500/15 text-zinc-600"));
  });

  it("flags security-relevant actions distinctly from routine updates", () => {
    expect(actionColor("pii_revealed")).not.toBe(actionColor("driver_updated"));
    expect(actionColor("refresh_token_reuse_detected")).toContain("red");
  });

  it("pretty-prints JSON details and passes non-JSON through", () => {
    expect(formatDetails(ZOHO_LOG.details)).toContain('"fields_changed"');
    expect(formatDetails(ZOHO_LOG.details).split("\n").length).toBeGreaterThan(1);
    expect(formatDetails("not json at all")).toBe("not json at all");
    expect(formatDetails(null)).toBe("—");
  });

  it("summarises details by the field an investigation looks for first", () => {
    expect(detailsSummary(ZOHO_LOG.details)).toBe(
      "fields_changed: client_id, client_secret, access_token",
    );
    expect(detailsSummary('{"updated_fields":["spinr_pass_enabled"]}')).toBe(
      "updated_fields: spinr_pass_enabled",
    );
    expect(detailsSummary("plain text")).toBe("plain text");
  });
});

describe("AuditLogsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getAuditLogs.mockResolvedValue([ZOHO_LOG]);
    getAuditLogTopActors.mockResolvedValue({ actors: [] });
    getAuditLogFacets.mockResolvedValue({
      days: 90,
      rows_scanned_capped: false,
      actions: [{ value: "zoho_desk_config_updated", count: 13 }],
      entity_types: [{ value: "zoho_desk_config", count: 84 }],
    });
  });

  it("builds its filter options from the data, not a hardcoded list", async () => {
    render(<AuditLogsPage />);
    await waitFor(() => expect(getAuditLogFacets).toHaveBeenCalled());
    expect(getAuditLogFacets).toHaveBeenCalledWith({ days: 90 });
  });

  it("renders the real action name rather than falling back to grey/unknown", async () => {
    render(<AuditLogsPage />);
    expect(await screen.findByText("Zoho desk config updated")).toBeInTheDocument();
  });

  it("surfaces fields_changed in the collapsed row", async () => {
    render(<AuditLogsPage />);
    expect(
      await screen.findByText("fields_changed: client_id, client_secret, access_token"),
    ).toBeInTheDocument();
  });

  it("expands a row to show request_id, actor and full details", async () => {
    const user = userEvent.setup();
    render(<AuditLogsPage />);
    await screen.findByText("Zoho desk config updated");

    // request_id is the join key to backend logs / Sentry — hidden until expanded.
    expect(screen.queryByText(ZOHO_LOG.request_id)).toBeNull();

    await user.click(screen.getByRole("button", { name: /expand details/i }));

    expect(screen.getByText(ZOHO_LOG.request_id)).toBeInTheDocument();
    expect(screen.getByText("super_admin")).toBeInTheDocument();
    expect(screen.getByText(/"client_secret"/)).toBeInTheDocument();
  });

  it("passes the date range through to the query", async () => {
    const user = userEvent.setup();
    render(<AuditLogsPage />);
    await waitFor(() => expect(getAuditLogs).toHaveBeenCalled());
    getAuditLogs.mockClear();

    await user.type(screen.getByLabelText(/From date/i), "2026-08-13");

    await waitFor(() => expect(getAuditLogs).toHaveBeenCalled());
    const opts = getAuditLogs.mock.calls.at(-1)![0] as Record<string, unknown>;
    expect(opts.start).toBeTruthy();
    expect(String(opts.start)).toContain("2026-08-13");
  });

  it("does not send date bounds when the range is empty", async () => {
    render(<AuditLogsPage />);
    await waitFor(() => expect(getAuditLogs).toHaveBeenCalled());
    const opts = getAuditLogs.mock.calls[0][0] as Record<string, unknown>;
    expect(opts.start).toBeUndefined();
    expect(opts.end).toBeUndefined();
  });
});
