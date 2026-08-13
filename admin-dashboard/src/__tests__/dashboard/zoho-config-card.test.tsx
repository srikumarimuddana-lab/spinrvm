/**
 * Regression cover for the 2026-08-13 Zoho credential-autofill incident.
 *
 * A browser password manager autofilled the admin's email + password into the
 * always-mounted "Client ID" / "Client Secret" inputs. A save intended only to
 * toggle the help-desk email signature shipped those values, overwriting the
 * working Zoho OAuth credentials — after which every token refresh failed with
 * Zoho's opaque `general_error`.
 *
 * The fix keeps the credential inputs unmounted unless the admin explicitly
 * opens the editor, so the ordinary save path has no autofill target at all.
 * These tests pin that behaviour.
 *
 * See docs/change-log/2026-08-13-zoho-credential-autofill-overwrite.md
 */

import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const getZohoConfig = vi.fn();
const updateZohoConfig = vi.fn();
const testZohoConnection = vi.fn();

vi.mock("@/lib/api", () => ({
  getZohoConfig: (...args: unknown[]) => getZohoConfig(...args),
  updateZohoConfig: (...args: unknown[]) => updateZohoConfig(...args),
  testZohoConnection: (...args: unknown[]) => testZohoConnection(...args),
}));

vi.mock("@/components/ui/use-toast", () => ({
  useToast: () => ({ toast: vi.fn() }),
}));

import { ZohoConfigCard } from "@/app/dashboard/support-tickets/_components/zoho-config-card";

const CONNECTED = {
  enabled: true,
  auto_sync_enabled: false,
  data_center: "com",
  org_id: "700123456",
  default_department_id: "",
  default_from_email: "support@spinr.ca",
  helpdesk_signature_enabled: false,
  helpdesk_email_signature: "",
  helpdesk_signature_preview: "",
  has_client_id: true,
  has_client_secret: true,
  has_refresh_token: true,
  connected: true,
};

/** Render and wait for the initial config load to settle. */
async function renderCard() {
  render(<ZohoConfigCard />);
  await screen.findByText("Zoho Desk Connection");
}

describe("ZohoConfigCard credential handling", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getZohoConfig.mockResolvedValue({ ...CONNECTED });
    updateZohoConfig.mockResolvedValue({ ...CONNECTED });
  });

  it("does not mount the credential inputs by default", async () => {
    await renderCard();
    expect(screen.queryByLabelText(/Client ID/i)).toBeNull();
    expect(screen.queryByLabelText(/Client Secret/i)).toBeNull();
    expect(screen.queryByLabelText(/Refresh Token/i)).toBeNull();
    // No password field at all — nothing for a password manager to target.
    expect(document.querySelectorAll('input[type="password"]')).toHaveLength(0);
  });

  it("omits credentials from an ordinary save", async () => {
    const user = userEvent.setup();
    await renderCard();

    await user.click(screen.getByRole("button", { name: /^Save$/ }));

    await waitFor(() => expect(updateZohoConfig).toHaveBeenCalledTimes(1));
    const body = updateZohoConfig.mock.calls[0][0] as Record<string, unknown>;
    expect(body).not.toHaveProperty("client_id");
    expect(body).not.toHaveProperty("client_secret");
    expect(body).not.toHaveProperty("refresh_token");
    // The non-credential fields still round-trip.
    expect(body.org_id).toBe("700123456");
    expect(body.data_center).toBe("com");
  });

  it("reveals the inputs with autofill suppressed once the editor is opened", async () => {
    const user = userEvent.setup();
    await renderCard();

    await user.click(screen.getByRole("button", { name: /Replace credentials/i }));

    const clientId = screen.getByLabelText(/Client ID/i);
    const clientSecret = screen.getByLabelText(/Client Secret/i);
    const refreshToken = screen.getByLabelText(/Refresh Token/i);

    for (const input of [clientId, clientSecret, refreshToken]) {
      expect(input).toHaveAttribute("data-1p-ignore");
      expect(input).toHaveAttribute("data-lpignore", "true");
      expect(input).toHaveAttribute("data-form-type", "other");
    }
    // Password fields must opt out of saved-credential autofill specifically.
    expect(clientSecret).toHaveAttribute("autocomplete", "new-password");
    expect(refreshToken).toHaveAttribute("autocomplete", "new-password");
    expect(clientId).toHaveAttribute("autocomplete", "off");
    // Names must not read as a username/password pair.
    expect(clientId).toHaveAttribute("name", "zoho-oauth-client-id");
    expect(clientSecret).toHaveAttribute("name", "zoho-oauth-client-secret");
  });

  it("sends credentials the admin actually typed", async () => {
    const user = userEvent.setup();
    await renderCard();

    await user.click(screen.getByRole("button", { name: /Replace credentials/i }));
    await user.type(screen.getByLabelText(/Client ID/i), "1000.NEWCLIENTID");
    await user.click(screen.getByRole("button", { name: /^Save$/ }));

    await waitFor(() => expect(updateZohoConfig).toHaveBeenCalledTimes(1));
    const body = updateZohoConfig.mock.calls[0][0] as Record<string, unknown>;
    expect(body.client_id).toBe("1000.NEWCLIENTID");
    // Untouched secrets are still omitted so the backend leaves them alone.
    expect(body).not.toHaveProperty("client_secret");
    expect(body).not.toHaveProperty("refresh_token");
  });

  it("discards typed credentials and re-collapses on cancel", async () => {
    const user = userEvent.setup();
    await renderCard();

    await user.click(screen.getByRole("button", { name: /Replace credentials/i }));
    await user.type(screen.getByLabelText(/Client ID/i), "1000.OOPS");
    await user.click(screen.getByRole("button", { name: /Cancel/i }));

    expect(screen.queryByLabelText(/Client ID/i)).toBeNull();

    await user.click(screen.getByRole("button", { name: /^Save$/ }));
    await waitFor(() => expect(updateZohoConfig).toHaveBeenCalledTimes(1));
    expect(updateZohoConfig.mock.calls[0][0]).not.toHaveProperty("client_id");
  });
});
