import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const mocks = vi.hoisted(() => ({
  replace: vi.fn(),
  sendCompanyOtp: vi.fn(),
  verifyCompanyOtp: vi.fn(),
  sendCompanyEmailOtp: vi.fn(),
  verifyCompanyEmailOtp: vi.fn(),
  getMyWorkProfiles: vi.fn(),
  acceptCompanyInvite: vi.fn(),
  completeLogin: vi.fn(),
  setMemberships: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/store/companyAuthStore", () => ({
  useCompanyAuthStore: (selector: (state: unknown) => unknown) =>
    selector({
      completeLogin: mocks.completeLogin,
      setMemberships: mocks.setMemberships,
    }),
}));

vi.mock("@/lib/companyApi", () => ({
  sendCompanyOtp: mocks.sendCompanyOtp,
  verifyCompanyOtp: mocks.verifyCompanyOtp,
  sendCompanyEmailOtp: mocks.sendCompanyEmailOtp,
  verifyCompanyEmailOtp: mocks.verifyCompanyEmailOtp,
  getMyWorkProfiles: mocks.getMyWorkProfiles,
  acceptCompanyInvite: mocks.acceptCompanyInvite,
  portalHome: (companyId: string) => `/company-portal/${companyId}/overview`,
}));

vi.mock("@/components/ui/button", () => ({
  Button: ({
    children,
    ...props
  }: React.PropsWithChildren<React.ButtonHTMLAttributes<HTMLButtonElement>>) => (
    <button {...props}>{children}</button>
  ),
}));

vi.mock("@/components/ui/input", () => ({
  Input: (props: React.InputHTMLAttributes<HTMLInputElement>) => <input {...props} />,
}));

vi.mock("@/components/ui/card", () => ({
  Card: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  CardContent: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  CardHeader: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  CardTitle: ({ children }: React.PropsWithChildren) => <h1>{children}</h1>,
}));

import CompanyLoginPage from "@/app/company-login/page";

describe("CompanyLoginPage", () => {
  beforeEach(() => {
    Object.values(mocks).forEach((mock) => {
      if (typeof mock === "function") mock.mockReset();
    });
    mocks.sendCompanyEmailOtp.mockResolvedValue(undefined);
    mocks.verifyCompanyEmailOtp.mockResolvedValue({
      token: "access-token",
      csrf_token: "csrf-token",
      user: { id: "user-1", email: "owner@acme.test" },
    });
    mocks.getMyWorkProfiles.mockResolvedValue([
      {
        company: { id: "company-1" },
        membership: { role: "owner" },
      },
    ]);
  });

  it("starts with a work email sign-in field", () => {
    render(<CompanyLoginPage />);

    expect(screen.getByText(/sign in with your work email/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText("name@company.com")).toHaveAttribute("type", "email");
  });

  it("sends the company email OTP and shows emailed-code copy", async () => {
    render(<CompanyLoginPage />);

    fireEvent.change(screen.getByPlaceholderText("name@company.com"), {
      target: { value: "Owner@Acme.Test " },
    });
    fireEvent.click(screen.getByRole("button", { name: /send code/i }));

    await waitFor(() => {
      expect(mocks.sendCompanyEmailOtp).toHaveBeenCalledWith("owner@acme.test");
    });
    expect(mocks.sendCompanyOtp).not.toHaveBeenCalled();
    expect(screen.getByText(/enter the code we emailed to owner@acme\.test/i)).toBeInTheDocument();
  });
});
