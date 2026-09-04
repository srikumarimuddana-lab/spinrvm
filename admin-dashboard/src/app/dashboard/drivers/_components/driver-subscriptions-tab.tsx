// Subscriptions tab of the drivers detail slideout: driver's Spinr Pass
// subscription payment history. Pure code motion out of drivers/page.tsx
// (design-audit follow-up, PR #4955's un-extracted remainder) -- no logic
// changes. Sort/pagination state stays owned by DriversPage (a separate
// sort instance from the main drivers list) and is passed down as props.
import { Pagination } from "@/components/ui/pagination";
import { formatCurrency } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHeader, TableRow } from "@/components/ui/table";
import { SortableHead, type SortState } from "@/components/ui/sortable-table";
import { Loader2 } from "lucide-react";

interface DriverSubscriptionsTabProps {
    subPaymentsLoading: boolean;
    driverSubPayments: any[];
    sortedSubPayments: any[];
    subPaymentsSort: SortState;
    toggleSubPaymentsSort: (key: string) => void;
    subPage: number;
    setSubPage: (page: number) => void;
    subPageSize: number;
    setSubPageSize: (size: number) => void;
}

export default function DriverSubscriptionsTab({
    subPaymentsLoading,
    driverSubPayments,
    sortedSubPayments,
    subPaymentsSort,
    toggleSubPaymentsSort,
    subPage,
    setSubPage,
    subPageSize,
    setSubPageSize,
}: DriverSubscriptionsTabProps) {
    return (
        <>
                                    {subPaymentsLoading ? (
                                        <div className="py-12 flex justify-center"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
                                    ) : driverSubPayments.length === 0 ? (
                                        <div className="py-12 text-center text-sm text-muted-foreground">No subscription payments found for this driver.</div>
                                    ) : (
                                        <>
                                        <Table>
                                            <TableHeader>
                                                <TableRow>
                                                    <SortableHead column="created_at" sort={subPaymentsSort} onSort={toggleSubPaymentsSort}>Date</SortableHead>
                                                    <SortableHead column="plan_name" sort={subPaymentsSort} onSort={toggleSubPaymentsSort}>Plan</SortableHead>
                                                    <SortableHead column="billing_reason" sort={subPaymentsSort} onSort={toggleSubPaymentsSort}>Type</SortableHead>
                                                    <SortableHead column="subtotal" sort={subPaymentsSort} onSort={toggleSubPaymentsSort} align="right">Subtotal</SortableHead>
                                                    <SortableHead column="gst_amount" sort={subPaymentsSort} onSort={toggleSubPaymentsSort} align="right">GST</SortableHead>
                                                    <SortableHead column="pst_amount" sort={subPaymentsSort} onSort={toggleSubPaymentsSort} align="right">PST</SortableHead>
                                                    <SortableHead column="hst_amount" sort={subPaymentsSort} onSort={toggleSubPaymentsSort} align="right">HST</SortableHead>
                                                    <SortableHead column="amount" sort={subPaymentsSort} onSort={toggleSubPaymentsSort} align="right">Total</SortableHead>
                                                </TableRow>
                                            </TableHeader>
                                            <TableBody>
                                                {sortedSubPayments.slice(subPage * subPageSize, (subPage + 1) * subPageSize).map((p) => (
                                                    <TableRow key={p.id}>
                                                        <TableCell className="text-xs whitespace-nowrap">
                                                            {p.created_at ? new Date(p.created_at).toLocaleDateString("en-CA", { year: "numeric", month: "short", day: "numeric" }) : "—"}
                                                        </TableCell>
                                                        <TableCell className="text-xs">{p.plan_name ?? "—"}</TableCell>
                                                        <TableCell>
                                                            <Badge variant="secondary" className="text-xs">
                                                                {p.billing_reason === "subscription_cycle" ? "Renewal" : p.billing_reason === "one_off" ? "One-off" : p.billing_reason ?? "—"}
                                                            </Badge>
                                                        </TableCell>
                                                        <TableCell className="text-right text-xs tabular-nums">{formatCurrency(p.subtotal)}</TableCell>
                                                        <TableCell className="text-right text-xs tabular-nums text-muted-foreground">{p.gst_amount > 0 ? formatCurrency(p.gst_amount) : "—"}</TableCell>
                                                        <TableCell className="text-right text-xs tabular-nums text-muted-foreground">{p.pst_amount > 0 ? formatCurrency(p.pst_amount) : "—"}</TableCell>
                                                        <TableCell className="text-right text-xs tabular-nums text-muted-foreground">{p.hst_amount > 0 ? formatCurrency(p.hst_amount) : "—"}</TableCell>
                                                        <TableCell className="text-right text-xs font-semibold tabular-nums">{formatCurrency(p.amount)}</TableCell>
                                                    </TableRow>
                                                ))}
                                            </TableBody>
                                        </Table>
                                        <div className="flex items-center justify-between gap-3 flex-wrap mt-3">
                                            <div className="flex items-center gap-2">
                                                <span className="text-xs text-muted-foreground">Show</span>
                                                <Select value={String(subPageSize)} onValueChange={(v) => { setSubPageSize(Number(v)); setSubPage(0); }}>
                                                    <SelectTrigger className="h-8 text-xs w-[70px]">
                                                        <SelectValue />
                                                    </SelectTrigger>
                                                    <SelectContent>
                                                        {[25, 50, 100].map(n => (
                                                            <SelectItem key={n} value={String(n)} className="text-xs">{n}</SelectItem>
                                                        ))}
                                                    </SelectContent>
                                                </Select>
                                                <span className="text-xs text-muted-foreground">per page</span>
                                            </div>
                                            {sortedSubPayments.length > subPageSize && (
                                                <Pagination
                                                    page={subPage}
                                                    pageSize={subPageSize}
                                                    hasNextPage={sortedSubPayments.length > (subPage + 1) * subPageSize}
                                                    totalCount={sortedSubPayments.length}
                                                    onPageChange={setSubPage}
                                                />
                                            )}
                                        </div>
                                        </>
                                    )}
        </>
    );
}
