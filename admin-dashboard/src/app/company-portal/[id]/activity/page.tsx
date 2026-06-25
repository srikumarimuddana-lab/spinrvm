"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import {
    BillingStatement,
    getCompanyBillingStatement,
} from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";

function currentMonth(): string {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function formatCAD(n: number | undefined) {
    return (n ?? 0).toLocaleString("en-CA", {
        style: "currency",
        currency: "CAD",
    });
}

const POLICY_RESULT_COLORS: Record<string, string> = {
    pass: "bg-emerald-100 text-emerald-800",
    override: "bg-blue-100 text-blue-800",
    fail: "bg-red-100 text-red-700",
};

export default function ActivityPage() {
    const { id } = useParams<{ id: string }>();
    const [month, setMonth] = useState<string>(currentMonth());
    const [memberFilter, setMemberFilter] = useState<string>("");
    const [statement, setStatement] = useState<BillingStatement | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        if (!id) return;
        setLoading(true);
        setError(null);
        try {
            const st = await getCompanyBillingStatement(id, month);
            setStatement(st);
        } catch (e) {
            setError(e instanceof Error ? e.message : "Failed to load");
        } finally {
            setLoading(false);
        }
    }, [id, month]);

    useEffect(() => {
        load();
    }, [load]);

    const rows = statement?.line_items ?? [];
    const filtered = memberFilter
        ? rows.filter((r) => r.member_id?.includes(memberFilter))
        : rows;
    const { sorted, sort, toggle } = useTableSort(filtered);

    return (
        <div className="space-y-6">
            <header>
                <h1 className="text-2xl font-semibold">Activity</h1>
                <p className="text-muted-foreground">
                    Every work-billed ride for the selected month.
                </p>
            </header>

            <div className="flex flex-wrap items-end gap-3">
                <div>
                    <Label htmlFor="month">Month</Label>
                    <Input
                        id="month"
                        type="month"
                        value={month}
                        onChange={(e) => setMonth(e.target.value)}
                    />
                </div>
                <div className="grow">
                    <Label htmlFor="mfilter">Filter by member id</Label>
                    <Input
                        id="mfilter"
                        placeholder="Paste member id…"
                        value={memberFilter}
                        onChange={(e) => setMemberFilter(e.target.value)}
                    />
                </div>
            </div>

            {error && (
                <p className="rounded bg-red-50 p-3 text-sm text-red-700">{error}</p>
            )}

            <Card>
                <CardContent className="p-0">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <SortableHead column="created_at" sort={sort} onSort={toggle}>Date</SortableHead>
                                <SortableHead column="member_id" sort={sort} onSort={toggle}>Member</SortableHead>
                                <SortableHead column="source_type" sort={sort} onSort={toggle}>Source</SortableHead>
                                <SortableHead column="allowance_debit_amount" sort={sort} onSort={toggle}>Allowance</SortableHead>
                                <SortableHead column="master_fallback_amount" sort={sort} onSort={toggle}>Master</SortableHead>
                                <SortableHead column="policy_check_result" sort={sort} onSort={toggle}>Policy</SortableHead>
                                <SortableHead column="allowance_debit_amount" sort={sort} onSort={toggle} align="right">Total</SortableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {loading && (
                                <TableRow>
                                    <TableCell colSpan={7} className="text-center text-muted-foreground">
                                        Loading…
                                    </TableCell>
                                </TableRow>
                            )}
                            {!loading && filtered.length === 0 && (
                                <TableRow>
                                    <TableCell colSpan={7} className="text-center text-muted-foreground">
                                        No rides for this filter.
                                    </TableCell>
                                </TableRow>
                            )}
                            {sorted.map((r) => (
                                <TableRow key={r.ride_id}>
                                    <TableCell className="text-xs text-muted-foreground">
                                        {r.created_at?.slice(0, 16).replace("T", " ")}
                                    </TableCell>
                                    <TableCell className="font-mono text-xs">
                                        {r.member_id}
                                    </TableCell>
                                    <TableCell className="text-xs">{r.source_type}</TableCell>
                                    <TableCell>{formatCAD(r.allowance_debit_amount)}</TableCell>
                                    <TableCell>{formatCAD(r.master_fallback_amount)}</TableCell>
                                    <TableCell>
                                        {r.policy_check_result && (
                                            <Badge
                                                className={
                                                    POLICY_RESULT_COLORS[r.policy_check_result] ?? ""
                                                }
                                            >
                                                {r.policy_check_result}
                                            </Badge>
                                        )}
                                    </TableCell>
                                    <TableCell className="text-right font-medium">
                                        {formatCAD(
                                            (r.allowance_debit_amount ?? 0) +
                                                (r.master_fallback_amount ?? 0)
                                        )}
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>
        </div>
    );
}
