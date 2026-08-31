/**
 * Company portal policy page — admin confirmation UX (GitHub #2683,
 * Phase B / Option 4). Before a policy save, the page checks how many
 * in-flight rides the edit would affect and only asks for confirmation
 * when that count is non-zero. No ride is ever touched by any of this —
 * it's purely a pre-save admin warning.
 */

import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("next/navigation", () => ({
    useParams: () => ({ id: "co-1" }),
}));

const getCompanyPolicy = vi.fn();
const getCompanyPolicyAffectedRidesCount = vi.fn();
const patchCompanyPolicy = vi.fn();

vi.mock("@/lib/companyApi", () => ({
    getCompanyPolicy: (...a: unknown[]) => getCompanyPolicy(...a),
    getCompanyPolicyAffectedRidesCount: (...a: unknown[]) =>
        getCompanyPolicyAffectedRidesCount(...a),
    patchCompanyPolicy: (...a: unknown[]) => patchCompanyPolicy(...a),
}));

// ---------- stub simple UI primitives (avoid pulling in the full design
// system for controls unrelated to this flow); AlertDialog is left as the
// real component since the confirmation dialog is exactly what's under test.
vi.mock("@/components/ui/card", () => ({
    Card: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
    CardContent: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
}));
vi.mock("@/components/ui/button", () => ({
    Button: ({ children, ...p }: React.PropsWithChildren<React.ButtonHTMLAttributes<HTMLButtonElement>>) => (
        <button {...p}>{children}</button>
    ),
    buttonVariants: () => "",
}));
vi.mock("@/components/ui/input", () => ({
    Input: (p: React.InputHTMLAttributes<HTMLInputElement>) => <input {...p} />,
}));
vi.mock("@/components/ui/label", () => ({
    Label: ({ children }: React.PropsWithChildren) => <label>{children}</label>,
}));
vi.mock("@/components/ui/badge", () => ({
    Badge: ({ children }: React.PropsWithChildren) => <span>{children}</span>,
}));
vi.mock("@/components/ui/switch", () => ({
    Switch: (p: React.InputHTMLAttributes<HTMLInputElement>) => <input type="checkbox" {...p} />,
}));
vi.mock("@/components/ui/select", () => ({
    Select: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
    SelectTrigger: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
    SelectValue: () => null,
    SelectContent: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
    SelectItem: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
}));

async function importPage() {
    const mod = await import("./page");
    return mod.default;
}

describe("company portal policy page — save confirmation", () => {
    beforeEach(() => {
        vi.resetModules();
        getCompanyPolicy.mockReset();
        getCompanyPolicyAffectedRidesCount.mockReset();
        patchCompanyPolicy.mockReset();
        getCompanyPolicy.mockResolvedValue({});
    });

    it("saves immediately with no dialog when the affected count is zero", async () => {
        getCompanyPolicyAffectedRidesCount.mockResolvedValue({ count: 0 });
        patchCompanyPolicy.mockResolvedValue({ active: true });

        const Page = await importPage();
        render(<Page />);

        const saveButton = await screen.findByText("Save policy");
        fireEvent.click(saveButton);

        await waitFor(() => {
            expect(patchCompanyPolicy).toHaveBeenCalledTimes(1);
        });
        expect(getCompanyPolicyAffectedRidesCount).toHaveBeenCalledWith("co-1");
        expect(screen.queryByText(/already booked/)).not.toBeInTheDocument();
    });

    it("shows the confirmation dialog and only saves after confirming", async () => {
        getCompanyPolicyAffectedRidesCount.mockResolvedValue({ count: 3 });
        patchCompanyPolicy.mockResolvedValue({ active: true });

        const Page = await importPage();
        render(<Page />);

        const saveButton = await screen.findByText("Save policy");
        fireEvent.click(saveButton);

        const dialogText = await screen.findByText(/affects 3 rides already booked/);
        expect(dialogText).toBeInTheDocument();
        expect(patchCompanyPolicy).not.toHaveBeenCalled();

        fireEvent.click(screen.getByText("Continue"));

        await waitFor(() => {
            expect(patchCompanyPolicy).toHaveBeenCalledTimes(1);
        });
    });

    it("does not save when the confirmation dialog is cancelled", async () => {
        getCompanyPolicyAffectedRidesCount.mockResolvedValue({ count: 2 });

        const Page = await importPage();
        render(<Page />);

        const saveButton = await screen.findByText("Save policy");
        fireEvent.click(saveButton);

        await screen.findByText(/affects 2 rides already booked/);
        fireEvent.click(screen.getByText("Cancel"));

        await waitFor(() => {
            expect(screen.queryByText(/already booked/)).not.toBeInTheDocument();
        });
        expect(patchCompanyPolicy).not.toHaveBeenCalled();
    });
});
