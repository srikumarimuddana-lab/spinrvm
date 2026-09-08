/**
 * MigrationChecklist — status panel for the legacy-migration tools list.
 *
 * Covers the base load/refresh/error paths, the progress counter ("N of M
 * done") and "Next up" callout (the tool array is already sorted by `order`
 * server-side; "next" is simply the first row not in the "done" state), and
 * the "just completed" highlight: a row whose state flips from not-"done" to
 * "done" between one load() and the next gets a brief visual treatment,
 * sourced from a sessionStorage snapshot (see MigrationChecklist.tsx's
 * `migration-checklist:last-tools` key).
 */

import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const adminGetMigrationStatus = vi.fn();

vi.mock("@/lib/api", () => ({
    adminGetMigrationStatus: (...a: unknown[]) => adminGetMigrationStatus(...a),
}));

vi.mock("@/components/ui/button", () => ({
    Button: ({ children, ...p }: React.PropsWithChildren<React.ButtonHTMLAttributes<HTMLButtonElement>>) => (
        <button {...p}>{children}</button>
    ),
}));
vi.mock("@/components/ui/card", () => ({
    Card: ({ children, ...p }: React.PropsWithChildren<React.HTMLAttributes<HTMLDivElement>>) => (
        <div {...p}>{children}</div>
    ),
    CardHeader: ({ children, ...p }: React.PropsWithChildren<React.HTMLAttributes<HTMLDivElement>>) => (
        <div {...p}>{children}</div>
    ),
    CardTitle: ({ children, ...p }: React.PropsWithChildren<React.HTMLAttributes<HTMLDivElement>>) => (
        <div {...p}>{children}</div>
    ),
    CardDescription: ({ children, ...p }: React.PropsWithChildren<React.HTMLAttributes<HTMLDivElement>>) => (
        <div {...p}>{children}</div>
    ),
    CardContent: ({ children, ...p }: React.PropsWithChildren<React.HTMLAttributes<HTMLDivElement>>) => (
        <div {...p}>{children}</div>
    ),
}));
vi.mock("lucide-react", () => ({
    CheckCircle2: () => <span />,
    Circle: () => <span />,
    AlertTriangle: () => <span />,
    HelpCircle: () => <span />,
    RefreshCw: () => <span />,
    Loader2: () => <span />,
}));

import { MigrationChecklist } from "./MigrationChecklist";
import type { MigrationToolStatus } from "@/lib/api";

const LAST_TOOLS_KEY = "migration-checklist:last-tools";

function tool(overrides: Partial<MigrationToolStatus> & Pick<MigrationToolStatus, "id" | "order">): MigrationToolStatus {
    return {
        name: `Tool ${overrides.id}`,
        state: "not_started",
        detail: "detail",
        admin_path: "/dashboard/bulk-operations",
        warning: null,
        ...overrides,
    };
}

beforeEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
    sessionStorage.clear();
});

describe("MigrationChecklist base behavior", () => {
    it("loads and renders tool rows with no highlight on the very first load of a session", async () => {
        adminGetMigrationStatus.mockResolvedValue({
            tools: [
                tool({ id: "a", order: 1, state: "done" }),
                tool({ id: "b", order: 2, state: "not_started" }),
            ],
        });

        render(<MigrationChecklist />);

        expect(await screen.findByText("Tool a")).toBeInTheDocument();
        // "Tool b" is the only not-done tool, so it's also the "Next up:"
        // callout target — appears twice (callout + its own row).
        expect(screen.getAllByText("Tool b")).toHaveLength(2);
        // No stored snapshot existed before this load, so nothing is "just completed" —
        // even though tool "a" is already done.
        expect(document.querySelector('[data-just-completed="true"]')).toBeNull();
    });

    it("surfaces a load failure", async () => {
        adminGetMigrationStatus.mockRejectedValueOnce(new Error("boom"));
        render(<MigrationChecklist />);
        expect(await screen.findByText("boom")).toBeInTheDocument();
    });

    it("re-fetches on manual refresh", async () => {
        adminGetMigrationStatus.mockResolvedValue({ tools: [tool({ id: "a", order: 1, state: "not_started" })] });
        render(<MigrationChecklist />);
        // The single tool is also "Next", so its name renders twice (row +
        // "Next up:" callout) — wait for both before interacting.
        await waitFor(() => expect(screen.getAllByText("Tool a")).toHaveLength(2));
        fireEvent.click(screen.getByRole("button", { name: /Refresh/ }));
        await waitFor(() => expect(adminGetMigrationStatus).toHaveBeenCalledTimes(2));
    });
});

describe("MigrationChecklist just-completed highlight", () => {
    it("highlights a row that flipped from not-done to done since the last load", async () => {
        sessionStorage.setItem(
            LAST_TOOLS_KEY,
            JSON.stringify([
                { id: "a", state: "not_started" },
                { id: "b", state: "done" },
            ]),
        );
        adminGetMigrationStatus.mockResolvedValue({
            tools: [
                tool({ id: "a", order: 1, state: "done" }),
                tool({ id: "b", order: 2, state: "done" }),
            ],
        });

        render(<MigrationChecklist />);
        await screen.findByText("Tool a");

        const rowA = screen.getByText("Tool a").closest("[data-just-completed]");
        expect(rowA).toHaveAttribute("data-just-completed", "true");
        expect(rowA).toHaveClass("bg-success/10");
    });

    it("does not highlight a tool that was already done on both loads", async () => {
        sessionStorage.setItem(LAST_TOOLS_KEY, JSON.stringify([{ id: "b", state: "done" }]));
        adminGetMigrationStatus.mockResolvedValue({
            tools: [tool({ id: "b", order: 2, state: "done" })],
        });

        render(<MigrationChecklist />);
        await screen.findByText("Tool b");

        expect(document.querySelector('[data-just-completed="true"]')).toBeNull();
    });
});

describe("MigrationChecklist progress + next-step", () => {
    it("shows the done count and highlights the first not-done tool as Next", async () => {
        adminGetMigrationStatus.mockResolvedValue({
            tools: [
                tool({ order: 1, id: "a", name: "Bulk Driver Import", state: "done" }),
                tool({ order: 2, id: "b", name: "Legacy Driver Import", state: "not_started" }),
                tool({ order: 3, id: "c", name: "Bulk Rider Import", state: "not_started" }),
            ],
        });

        render(<MigrationChecklist />);

        expect(await screen.findByText("(1 of 3 done)")).toBeInTheDocument();
        // "Legacy Driver Import" appears twice: the "Next up:" callout link
        // and its own row link.
        expect(screen.getAllByText("Legacy Driver Import")).toHaveLength(2);
        expect(screen.getByText("Next")).toBeInTheDocument();
    });

    it("shows an all-done message once every tool is done", async () => {
        adminGetMigrationStatus.mockResolvedValue({
            tools: [
                tool({ order: 1, id: "a", name: "Bulk Driver Import", state: "done" }),
                tool({ order: 2, id: "b", name: "Legacy Driver Import", state: "done" }),
            ],
        });

        render(<MigrationChecklist />);

        expect(await screen.findByText("(2 of 2 done)")).toBeInTheDocument();
        expect(screen.getByText("All steps are done.")).toBeInTheDocument();
        expect(screen.queryByText("Next")).not.toBeInTheDocument();
    });

    it("treats partial and manual_check_required as not-done for the Next pick", async () => {
        adminGetMigrationStatus.mockResolvedValue({
            tools: [
                tool({ order: 1, id: "a", name: "Bulk Driver Import", state: "done" }),
                tool({ order: 2, id: "b", name: "Legacy Driver Import", state: "partial" }),
                tool({ order: 3, id: "c", name: "Bulk Rider Import", state: "manual_check_required" }),
            ],
        });

        render(<MigrationChecklist />);

        expect(await screen.findByText("(1 of 3 done)")).toBeInTheDocument();
        // The first not-done tool by order (#2, partial) is Next, not #3.
        expect(screen.getAllByText("Legacy Driver Import")).toHaveLength(2);
        expect(screen.getAllByText("Next")).toHaveLength(1);
    });
});
