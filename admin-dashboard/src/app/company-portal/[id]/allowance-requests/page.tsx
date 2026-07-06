"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import type {
    AllowanceRequestRow,
} from "@/lib/api";
import {
    decideAllowanceRequest,
    listCompanyAllowanceRequests,
} from "@/lib/companyApi";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import {
    Dialog,
    DialogContent,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { Check, X } from "lucide-react";

type Filter = AllowanceRequestRow["status"] | "all";

const STATUS_COLORS: Record<AllowanceRequestRow["status"], string> = {
    pending: "bg-yellow-100 text-yellow-800",
    approved: "bg-emerald-100 text-emerald-800",
    auto_approved: "bg-blue-100 text-blue-800",
    denied: "bg-red-100 text-red-700",
};

export default function AllowanceRequestsPage() {
    const { id } = useParams<{ id: string }>();
    const [rows, setRows] = useState<AllowanceRequestRow[]>([]);
    const [filter, setFilter] = useState<Filter>("pending");
    const [loading, setLoading] = useState(true);
    const [decision, setDecision] = useState<{ row: AllowanceRequestRow; approve: boolean } | null>(null);
    const [note, setNote] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const { sorted, sort, toggle } = useTableSort(rows);

    const load = useCallback(async () => {
        if (!id) return;
        setLoading(true);
        try {
            const data = await listCompanyAllowanceRequests(id, filter === "all" ? "" : filter);
            setRows(data);
        } catch (e) {
            setError(e instanceof Error ? e.message : "Failed to load");
        } finally {
            setLoading(false);
        }
    }, [id, filter]);

    useEffect(() => {
        load();
    }, [load]);

    const submitDecision = async () => {
        if (!id || !decision) return;
        setBusy(true);
        setError(null);
        try {
            await decideAllowanceRequest(id, decision.row.id, {
                approve: decision.approve,
                note: note.trim() || undefined,
            });
            setDecision(null);
            setNote("");
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : "Failed");
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="space-y-6">
            <header>
                <h1 className="text-2xl font-semibold">Allowance Requests</h1>
                <p className="text-muted-foreground">
                    Approve or deny requests from members for additional funds.
                </p>
            </header>

            <div className="flex gap-2">
                {(["pending", "approved", "denied", "all"] as Filter[]).map((f) => (
                    <Button
                        key={f}
                        size="sm"
                        variant={filter === f ? "default" : "outline"}
                        onClick={() => setFilter(f)}
                        className="capitalize"
                    >
                        {f}
                    </Button>
                ))}
            </div>

            <Card>
                <CardContent className="p-0">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <SortableHead column="amount" sort={sort} onSort={toggle}>Amount</SortableHead>
                                <SortableHead column="reason" sort={sort} onSort={toggle}>Reason</SortableHead>
                                <SortableHead column="status" sort={sort} onSort={toggle}>Status</SortableHead>
                                <SortableHead column="created_at" sort={sort} onSort={toggle}>Requested</SortableHead>
                                <TableHead className="text-right">Actions</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {loading && (
                                <TableRow>
                                    <TableCell colSpan={5} className="text-center text-muted-foreground">
                                        Loading…
                                    </TableCell>
                                </TableRow>
                            )}
                            {!loading && rows.length === 0 && (
                                <TableRow>
                                    <TableCell colSpan={5} className="text-center text-muted-foreground">
                                        No requests.
                                    </TableCell>
                                </TableRow>
                            )}
                            {sorted.map((r) => (
                                <TableRow key={r.id}>
                                    <TableCell className="font-medium">
                                        {r.amount.toLocaleString("en-CA", {
                                            style: "currency",
                                            currency: "CAD",
                                        })}
                                    </TableCell>
                                    <TableCell className="max-w-[40ch] truncate">
                                        {r.reason}
                                    </TableCell>
                                    <TableCell>
                                        <Badge className={STATUS_COLORS[r.status]}>
                                            {r.status}
                                        </Badge>
                                    </TableCell>
                                    <TableCell className="text-xs text-muted-foreground">
                                        {r.created_at?.slice(0, 10)}
                                    </TableCell>
                                    <TableCell className="text-right">
                                        {r.status === "pending" && (
                                            <div className="flex justify-end gap-2">
                                                <Button
                                                    size="sm"
                                                    variant="default"
                                                    onClick={() => setDecision({ row: r, approve: true })}
                                                >
                                                    <Check className="mr-1 h-3.5 w-3.5" /> Approve
                                                </Button>
                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    onClick={() => setDecision({ row: r, approve: false })}
                                                >
                                                    <X className="mr-1 h-3.5 w-3.5" /> Deny
                                                </Button>
                                            </div>
                                        )}
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>

            {error && (
                <p className="rounded bg-red-50 p-3 text-sm text-red-700">{error}</p>
            )}

            <Dialog open={!!decision} onOpenChange={(open) => !open && setDecision(null)}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>
                            {decision?.approve ? "Approve request" : "Deny request"}
                        </DialogTitle>
                    </DialogHeader>
                    <div className="space-y-2 py-2 text-sm">
                        <p>
                            <span className="text-muted-foreground">Amount: </span>
                            <span className="font-medium">
                                {decision?.row.amount.toLocaleString("en-CA", {
                                    style: "currency",
                                    currency: "CAD",
                                })}
                            </span>
                        </p>
                        <p>
                            <span className="text-muted-foreground">Reason: </span>
                            {decision?.row.reason}
                        </p>
                        <Textarea
                            placeholder="Optional note for the member…"
                            value={note}
                            onChange={(e) => setNote(e.target.value)}
                        />
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setDecision(null)}>
                            Cancel
                        </Button>
                        <Button onClick={submitDecision} disabled={busy}>
                            {busy ? "Submitting…" : decision?.approve ? "Approve" : "Deny"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
