import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Users } from "lucide-react";
import { EmptyState } from "../empty-state";

describe("EmptyState", () => {
    it("renders the title as an h2 by default, with the hint", () => {
        render(<EmptyState icon={Users} title="No staff members yet" description="Add your first team member" />);
        expect(screen.getByRole("heading", { level: 2, name: "No staff members yet" })).toBeInTheDocument();
        expect(screen.getByText("Add your first team member")).toBeInTheDocument();
    });

    it("renders an h3 when the list sits under a section heading", () => {
        render(<EmptyState icon={Users} title="No scheduled messages" headingLevel={3} />);
        expect(screen.getByRole("heading", { level: 3, name: "No scheduled messages" })).toBeInTheDocument();
        expect(screen.queryByRole("heading", { level: 2 })).not.toBeInTheDocument();
    });

    it("keeps the icon out of the accessibility tree", () => {
        const { container } = render(<EmptyState icon={Users} title="No staff members yet" />);
        expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    });

    it("renders the same DOM as the hand-rolled block it replaces", () => {
        // The markup staff/page.tsx shipped before moving to EmptyState.
        const legacy = render(
            <div className="text-center py-16">
                <Users className="h-12 w-12 text-muted-foreground/30 mx-auto mb-4" />
                <h2 className="text-lg font-semibold">No staff members yet</h2>
                <p className="text-muted-foreground mt-1">Add your first team member to share admin access</p>
            </div>,
        );
        const shared = render(
            <EmptyState
                icon={Users}
                title="No staff members yet"
                description="Add your first team member to share admin access"
            />,
        );
        expect(shared.container.innerHTML).toBe(legacy.container.innerHTML);
    });
});
