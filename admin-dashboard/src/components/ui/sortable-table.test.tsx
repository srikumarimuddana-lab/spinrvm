/**
 * SortableHead exposes the current sort to assistive tech via aria-sort on
 * the header cell (WCAG 2.1 SC 1.3.1 / 4.1.2); the chevron icon alone is
 * visual only.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Table, TableHeader, TableRow } from "@/components/ui/table";
import { SortableHead, type SortState } from "@/components/ui/sortable-table";

function renderHead(sort: SortState, onSort = vi.fn()) {
    render(
        <Table>
            <TableHeader>
                <TableRow>
                    <SortableHead column="created_at" sort={sort} onSort={onSort}>
                        Date
                    </SortableHead>
                </TableRow>
            </TableHeader>
        </Table>,
    );
    return { th: screen.getByRole("columnheader"), onSort };
}

describe("SortableHead aria-sort", () => {
    it('is "none" when the column is not the active sort', () => {
        const { th } = renderHead({ key: "amount", dir: "asc" });
        expect(th).toHaveAttribute("aria-sort", "none");
    });

    it('is "ascending" for the active column sorted ascending', () => {
        const { th } = renderHead({ key: "created_at", dir: "asc" });
        expect(th).toHaveAttribute("aria-sort", "ascending");
    });

    it('is "descending" for the active column sorted descending', () => {
        const { th } = renderHead({ key: "created_at", dir: "desc" });
        expect(th).toHaveAttribute("aria-sort", "descending");
    });

    it("still sorts by its column when the header button is pressed", () => {
        const { onSort } = renderHead({ key: null, dir: "asc" });
        fireEvent.click(screen.getByRole("button", { name: "Sort by Date" }));
        expect(onSort).toHaveBeenCalledWith("created_at");
    });
});
