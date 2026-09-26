/**
 * UX program W1.4: the 404 page must not send public visitors (tracking-link
 * viewers, driver applicants, company users) to the staff dashboard, which
 * bounces them to the staff login.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

let mockPathname: string | null = "/";

vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
}));

import NotFound from "./not-found";

function renderAt(pathname: string | null) {
  mockPathname = pathname;
  return render(<NotFound />);
}

describe("NotFound", () => {
  it("keeps the dashboard link for staff routes", () => {
    renderAt("/dashboard/no-such-page");
    expect(screen.getByRole("link", { name: "Go to Dashboard" })).toHaveAttribute("href", "/dashboard");
  });

  it("shows an expired-link message and no dashboard link on a tracking path", () => {
    renderAt("/track/abc/extra");
    expect(screen.getByText(/tracking link is invalid or has expired/i)).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("sends driver applicants to the sign-up page", () => {
    renderAt("/register/drivers");
    expect(screen.getByRole("link", { name: "Go to driver sign-up" })).toHaveAttribute("href", "/register/driver");
  });

  it("sends company users to the company portal", () => {
    renderAt("/company-portal/abc/extra");
    expect(screen.getByRole("link", { name: "Go to company portal" })).toHaveAttribute("href", "/company-portal");
  });

  it("falls back to the dashboard link when the path is unknown", () => {
    renderAt(null);
    expect(screen.getByRole("link", { name: "Go to Dashboard" })).toBeInTheDocument();
  });
});
