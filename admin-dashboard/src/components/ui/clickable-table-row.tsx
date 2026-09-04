"use client";

import * as React from "react";
import { TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";

/**
 * A <TableRow> that behaves as a keyboard-operable clickable control:
 * tabIndex, aria-label, Enter/Space activation, and a visible focus ring.
 *
 * This is the shared version of the onClick/tabIndex/onKeyDown boilerplate
 * that was hand-rolled independently on ~12 admin pages after the
 * design-audit keyboard-accessibility fix (2026-09-04, PR #4935). Use this
 * for any new clickable row instead of copying that pattern by hand — that
 * was the audit's own recommendation: make the correct behavior the default,
 * not an opt-in every page has to remember.
 *
 * Always guards against double-activation when the row contains its own
 * nested interactive controls (e.g. an inline "view ride" button) by
 * ignoring any keydown that didn't originate on the row itself — this is a
 * no-op for rows with no nested interactive elements, and load-bearing for
 * rows that have them (see docs/change-log/2026-09-04-a11y-keyboard-touch-target-fixes.md
 * for the bug this guards against).
 *
 * Pass `active={false}` for a row that's only conditionally clickable (e.g.
 * only rows matching some predicate are interactive) — it renders as a
 * plain, non-interactive TableRow with none of the click/keyboard wiring
 * attached, matching audit-logs/page.tsx's "only long rows expand" pattern.
 */
export function ClickableTableRow({
  onActivate,
  ariaLabel,
  active = true,
  className,
  children,
  ...props
}: Omit<
  React.ComponentProps<typeof TableRow>,
  "onClick" | "tabIndex" | "aria-label" | "onKeyDown"
> & {
  onActivate: () => void;
  ariaLabel: string;
  active?: boolean;
}) {
  if (!active) {
    return (
      <TableRow className={className} {...props}>
        {children}
      </TableRow>
    );
  }

  return (
    <TableRow
      className={cn(
        "cursor-pointer focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ring",
        className
      )}
      onClick={onActivate}
      tabIndex={0}
      aria-label={ariaLabel}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onActivate();
        }
      }}
      {...props}
    >
      {children}
    </TableRow>
  );
}
