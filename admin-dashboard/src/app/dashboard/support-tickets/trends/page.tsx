"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
    ResponsiveContainer,
    LineChart,
    Line,
    BarChart,
    Bar,
    PieChart,
    Pie,
    Cell,
    XAxis,
    YAxis,
    CartesianGrid,
    Tooltip,
    Legend,
} from "recharts";
import { getDeskTrends, ZohoTrends } from "@/lib/api";
import { useRequireModule } from "@/hooks/useRequireModule";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { ArrowLeft, BarChart3, Info } from "lucide-react";

const COLORS = ["#3b82f6", "#f59e0b", "#ef4444", "#10b981", "#8b5cf6", "#64748b"];

function toSeries(rec: Record<string, number>) {
    return Object.entries(rec).map(([name, value]) => ({ name, value }));
}

export default function TrendsPage() {
    const { allowed } = useRequireModule("support_tickets");
    const [days, setDays] = useState("14");
    const [data, setData] = useState<ZohoTrends | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const load = async () => {
        setLoading(true);
        setError(null);
        try {
            setData(await getDeskTrends({ days: Number(days) }));
        } catch (e: any) {
            setError(e?.message || "Failed to load trends");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        if (allowed) load();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [allowed, days]);

    if (!allowed) return null;

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="flex items-center gap-2 text-2xl font-bold">
                        <BarChart3 className="h-6 w-6" /> Ticket Trends
                    </h1>
                    <p className="text-sm text-muted-foreground">Volume and breakdowns over time</p>
                </div>
                <div className="flex items-center gap-2">
                    <Select value={days} onValueChange={setDays}>
                        <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
                        <SelectContent>
                            <SelectItem value="7">Last 7 days</SelectItem>
                            <SelectItem value="14">Last 14 days</SelectItem>
                            <SelectItem value="30">Last 30 days</SelectItem>
                            <SelectItem value="90">Last 90 days</SelectItem>
                        </SelectContent>
                    </Select>
                    <Button asChild variant="outline">
                        <Link href="/dashboard/support-tickets"><ArrowLeft className="mr-2 h-4 w-4" /> Back</Link>
                    </Button>
                </div>
            </div>

            {loading && <Card><CardContent className="p-6 text-muted-foreground">Loading…</CardContent></Card>}
            {!loading && error && <Card><CardContent className="p-4 text-sm text-red-600">{error}</CardContent></Card>}

            {!loading && data && (
                <>
                    {data.approximate && (
                        <Card>
                            <CardContent className="flex items-center gap-2 p-3 text-xs text-muted-foreground">
                                <Info className="h-4 w-4" />
                                Trends are computed from the latest {data.sample_size} tickets and are approximate at high volume.
                            </CardContent>
                        </Card>
                    )}

                    <Card>
                        <CardHeader><CardTitle className="text-base">Ticket volume (by day created)</CardTitle></CardHeader>
                        <CardContent>
                            <ResponsiveContainer width="100%" height={280}>
                                <LineChart data={data.volume} margin={{ top: 10, right: 16, left: -8, bottom: 0 }}>
                                    <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                                    <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                                    <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                                    <Tooltip />
                                    <Line type="monotone" dataKey="count" stroke="#3b82f6" strokeWidth={2} dot={{ r: 2 }} />
                                </LineChart>
                            </ResponsiveContainer>
                        </CardContent>
                    </Card>

                    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                        <Card>
                            <CardHeader><CardTitle className="text-base">By status</CardTitle></CardHeader>
                            <CardContent>
                                <ResponsiveContainer width="100%" height={240}>
                                    <PieChart>
                                        <Pie data={toSeries(data.by_status)} dataKey="value" nameKey="name" outerRadius={80} label>
                                            {toSeries(data.by_status).map((_, i) => (
                                                <Cell key={i} fill={COLORS[i % COLORS.length]} />
                                            ))}
                                        </Pie>
                                        <Legend />
                                        <Tooltip />
                                    </PieChart>
                                </ResponsiveContainer>
                            </CardContent>
                        </Card>

                        <Card>
                            <CardHeader><CardTitle className="text-base">By priority</CardTitle></CardHeader>
                            <CardContent>
                                <ResponsiveContainer width="100%" height={240}>
                                    <BarChart data={toSeries(data.by_priority)}>
                                        <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                                        <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                                        <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                                        <Tooltip />
                                        <Bar dataKey="value" fill="#f59e0b" />
                                    </BarChart>
                                </ResponsiveContainer>
                            </CardContent>
                        </Card>

                        <Card>
                            <CardHeader><CardTitle className="text-base">By channel</CardTitle></CardHeader>
                            <CardContent>
                                <ResponsiveContainer width="100%" height={240}>
                                    <BarChart data={toSeries(data.by_channel)}>
                                        <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                                        <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                                        <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                                        <Tooltip />
                                        <Bar dataKey="value" fill="#10b981" />
                                    </BarChart>
                                </ResponsiveContainer>
                            </CardContent>
                        </Card>
                    </div>
                </>
            )}
        </div>
    );
}
