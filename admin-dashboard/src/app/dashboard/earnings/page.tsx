"use client";

import { useEffect, useState } from "react";
import { getEarnings } from "@/lib/api";
import { exportToCsv } from "@/lib/export-csv";
import { formatCurrency, formatDate, statusColor } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { Download } from "lucide-react";
import { Input } from "@/components/ui/input";

export default function EarningsPage() {
    const [earnings, setEarnings] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [dateFrom, setDateFrom] = useState("");
    const [dateTo, setDateTo] = useState("");

    useEffect(() => {
        getEarnings()
            .then(setEarnings)
            .catch(() => { })
            .finally(() => setLoading(false));
    }, []);

    const filtered = earnings.filter((e) => {
        if (!dateFrom && !dateTo) return true;
        const d = e.date ? new Date(e.date).toISOString().split("T")[0] : "";
        if (dateFrom && d < dateFrom) return false;
        if (dateTo && d > dateTo) return false;
        return true;
    });

    const totals = filtered.reduce(
        (acc, e) => ({
            totalFare: acc.totalFare + (e.total_fare || 0),
            driverEarnings: acc.driverEarnings + (e.driver_earnings || 0),
            adminEarnings: acc.adminEarnings + (e.admin_earnings || 0),
            tips: acc.tips + (e.tip_amount || 0),
        }),
        { totalFare: 0, driverEarnings: 0, adminEarnings: 0, tips: 0 }
    );

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Earnings</h1>
                    <p className="text-muted-foreground mt-1">
                        Detailed breakdown of all platform earnings.
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="w-36 text-xs" />
                    <span className="text-muted-foreground text-sm">to</span>
                    <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="w-36 text-xs" />
                    <Button
                        variant="outline"
                        onClick={() => exportToCsv("earnings", filtered, [
                            { key: "ride_id", label: "Ride ID" },
                            { key: "status", label: "Status" },
                            { key: "total_fare", label: "Total Fare" },
                            { key: "driver_earnings", label: "Driver Earnings" },
                            { key: "admin_earnings", label: "Platform Revenue" },
                            { key: "tip_amount", label: "Tip" },
                            { key: "stripe_transaction_id", label: "Stripe Transaction ID" },
                            { key: "date", label: "Date" },
                        ])}
                        disabled={filtered.length === 0}
                    >
                        <Download className="mr-2 h-4 w-4" /> Export CSV
                    </Button>
                </div>
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <Card className="border-border/50">
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm text-muted-foreground">Total Fares</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-2xl font-bold">{formatCurrency(totals.totalFare)}</p>
                    </CardContent>
                </Card>
                <Card className="border-border/50">
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm text-muted-foreground">Driver Earnings</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-2xl font-bold text-emerald-500">
                            {formatCurrency(totals.driverEarnings)}
                        </p>
                    </CardContent>
                </Card>
                <Card className="border-border/50">
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm text-muted-foreground">Platform Revenue</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-2xl font-bold text-violet-500">
                            {formatCurrency(totals.adminEarnings)}
                        </p>
                    </CardContent>
                </Card>
                <Card className="border-border/50">
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm text-muted-foreground">Tips</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-2xl font-bold text-amber-500">
                            {formatCurrency(totals.tips)}
                        </p>
                    </CardContent>
                </Card>
            </div>

            <Card className="border-border/50">
                <CardContent className="p-0">
                    {loading ? (
                        <div className="flex items-center justify-center p-12">
                            <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                        </div>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Ride ID</TableHead>
                                    <TableHead>Status</TableHead>
                                    <TableHead>Total Fare</TableHead>
                                    <TableHead>Driver</TableHead>
                                    <TableHead>Platform</TableHead>
                                    <TableHead>Tip</TableHead>
                                    <TableHead>Date</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {filtered.length === 0 ? (
                                    <TableRow>
                                        <TableCell colSpan={7} className="text-center text-muted-foreground py-12">
                                            No earnings data yet.
                                        </TableCell>
                                    </TableRow>
                                ) : (
                                    filtered.map((e) => (
                                        <TableRow key={e.ride_id}>
                                            <TableCell className="font-mono text-xs">
                                                {e.ride_id?.slice(0, 8)}...
                                            </TableCell>
                                            <TableCell>
                                                <Badge variant="secondary" className={statusColor(e.status)}>
                                                    {e.status?.replace(/_/g, " ")}
                                                </Badge>
                                            </TableCell>
                                            <TableCell>{formatCurrency(e.total_fare || 0)}</TableCell>
                                            <TableCell className="text-emerald-500">
                                                {formatCurrency(e.driver_earnings || 0)}
                                            </TableCell>
                                            <TableCell className="text-violet-500">
                                                {formatCurrency(e.admin_earnings || 0)}
                                            </TableCell>
                                            <TableCell className="text-amber-500">
                                                {formatCurrency(e.tip_amount || 0)}
                                            </TableCell>
                                            <TableCell className="text-xs text-muted-foreground">
                                                {formatDate(e.date)}
                                            </TableCell>
                                        </TableRow>
                                    ))
                                )}
                            </TableBody>
                        </Table>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
