/**
 * Shared error/warning table for Bulk Import tools, generalized from the
 * Legacy Tax-ID Backfill pilot. Every tool's report items share the same
 * {ref, field, message} shape (just under different key names --
 * old_driver_id, row_num, row_ref, ...) -- this renders that shape once, with
 * an optional plain-language explanation (cause + fix) under each message
 * for tools that have an error-help map (see lib/bulk-import-error-help.ts).
 * A tool with no explainer, or a message with no mapped entry, still renders
 * correctly -- the extra line simply doesn't appear.
 */

import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";

export interface IssueExplanation {
    cause: string;
    fix: string;
}

export interface IssueTableProps<T> {
    items: T[];
    getRowKey: (item: T, index: number) => string;
    getRef: (item: T) => string;
    getField: (item: T) => string;
    getMessage: (item: T) => string;
    /** Column header for the ref column, e.g. "old_driver_id" or "Row". */
    refLabel: string;
    explain?: (message: string) => IssueExplanation | null;
}

export function IssueTable<T>({ items, getRowKey, getRef, getField, getMessage, refLabel, explain }: IssueTableProps<T>) {
    return (
        <div className="overflow-x-auto rounded-md border">
            <Table>
                <TableHeader>
                    <TableRow>
                        <TableHead className="w-40">{refLabel}</TableHead>
                        <TableHead className="w-48">Field</TableHead>
                        <TableHead>Message</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {items.map((item, i) => {
                        const message = getMessage(item);
                        const explanation = explain ? explain(message) : null;
                        return (
                            <TableRow key={getRowKey(item, i)}>
                                <TableCell className="font-mono text-xs align-top">{getRef(item)}</TableCell>
                                <TableCell className="font-mono text-xs align-top">{getField(item)}</TableCell>
                                <TableCell className="text-sm">
                                    <p>{message}</p>
                                    {explanation ? (
                                        <div className="mt-1.5 space-y-0.5 rounded border-l-2 border-muted-foreground/30 pl-2 text-xs text-muted-foreground">
                                            <p>{explanation.cause}</p>
                                            <p className="font-medium">What to do: {explanation.fix}</p>
                                        </div>
                                    ) : null}
                                </TableCell>
                            </TableRow>
                        );
                    })}
                </TableBody>
            </Table>
        </div>
    );
}
