/**
 * MigrationChecklist — covers the progress counter ("N of M done") and the
 * "Next up" callout added alongside the always-reachable-now checklist
 * (the tool array is already sorted by `order` server-side; "next" is
 * simply the first row not in the "done" state).
 */

import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

const adminGetMigrationStatus = vi.fn();

vi.mock("@/lib/api", () => ({
    adminGetMigrationStatus: (...a: unknown[]) => adminGetMigrationStatus(...a),
}));

import { MigrationChecklist } from "./MigrationChecklist";
import type { MigrationToolStatus } from "@/lib/api";

function tool(overrides: Partial<MigrationToolStatus>): MigrationToolStatus {
    return {
        order: 1,
        id: "tool",
        name: "Tool",
        state: "not_started",
        detail: "detail",
        admin_path: "/dashboard/somewhere",
        warning: null,
        ...overrides,
    };
}

beforeEach(() => {
    vi.clearAllMocks();
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
