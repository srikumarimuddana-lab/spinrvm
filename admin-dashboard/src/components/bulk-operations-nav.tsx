"use client";

/**
 * Consistent "return to the Migration Checklist" link for every standalone
 * migration/bulk-import tool page (Legacy Driver Import, SIN/DOB Backfill,
 * Vehicle-History Backfill, Bulk Driver Import, Legacy Saved-Address
 * Backfill, ...). Before this existed, most of these pages had no way back
 * to Records & Compliance -> Bulk Import short of the sidebar/browser back
 * button, which was especially confusing right after a successful commit —
 * the Migration Checklist already reflects the new state on load (it reads
 * live DB counts), the operator just had no path back to see it go green.
 *
 * `/dashboard/bulk-operations` still works (next.config.ts redirects it to
 * this URL), but this is the canonical, currently-linked-in-nav address —
 * see sidebar.tsx's Records & Compliance entry.
 */

import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";

export const BULK_OPERATIONS_HREF = "/dashboard/records?tab=bulk-operations";

export function BackToMigrationChecklistLink({ className }: { className?: string }) {
    return (
        <Button asChild variant="ghost" size="sm" className={className}>
            <Link href={BULK_OPERATIONS_HREF}>
                <ArrowLeft className="mr-2 h-4 w-4" /> Back to Migration Checklist
            </Link>
        </Button>
    );
}
