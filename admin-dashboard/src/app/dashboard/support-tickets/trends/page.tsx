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
import { getDeskTrends, getDeskAgents, ZohoTrends } from "@/lib/api";
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

function toSeries(rec: Record<string, number> | null | undefined) {
    return Object.entries(rec ?? {}).map(([name, value]) => ({ name, value }));
}

function fmtHours(h: number | null): string {
    if (h == null) return "—";
    if (h < 1) return `${Math.round(h * 60)}m`;
    if (h < 48) return `${h.toFixed(1)}h`;
    return `${(h / 24).toFixed(1)}d`;
}

export default function TrendsPage() {
    const { allowed } = useRequireModule("support_tickets");
    const [days, setDays] = useState("14");
    const [assignee, setAssignee] = useState("all");
    const [agents, setAgents] = useState<any[]>([]);
    const [data, setData] = useState<ZohoTrends | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const load = async () => {
        setLoading(true);
        setError(null);
        try {
            setData(
                await getDeskTrends({
                    days: Number(days),
                    assigneeId: assignee === "all" ? undefined : assignee,
                }),
            );
        } catch (e: any) {
            setError(e?.message || "Failed to load trends");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        if (allowed) load();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [allowed, days, assignee]);

    useEffect(() => {
        if (!allowed) return;
        getDeskAgents().then((r) => setAgents(r.data || [])).catch(() => {});
    }, [allowed]);

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
                <div className="flex flex-wrap items-center gap-2">
                    <Select value={assignee} onValueChange={setAssignee}>
                        <SelectTrigger className="w-44"><SelectValue placeholder="Assignee" /></SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">All assignees</SelectItem>
                            {agents.map((a) => (
                                <SelectItem key={a.id} value={a.id}>
                                    {`${a.firstName || ""} ${a.lastName || ""}`.trim() || a.emailId || a.id}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
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

                    {/* Level stats for the selected window / assignee */}
                    <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
                        <StatCard label="Opened" value={String(data.stats?.opened ?? 0)} />
                        <StatCard label="Closed" value={String(data.stats?.closed ?? 0)} />
                        <StatCard label="Still open" value={String(data.stats?.open_now ?? 0)} accent />
                        <StatCard label="Avg resolution" value={fmtHours(data.stats?.avg_resolution_hours ?? null)} />
                        <StatCard label="Median resolution" value={fmtHours(data.stats?.median_resolution_hours ?? null)} />
                    </div>

                    <Card>
                        <CardHeader>
                            <CardTitle className="text-base">Opened vs closed (by day)</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <ResponsiveContainer width="100%" height={280}>
                                <LineChart data={data.volume} margin={{ top: 10, right: 16, left: -8, bottom: 0 }}>
                                    <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                                    <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                                    <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                                    <Tooltip />
                                    <Legend />
                                    <Line type="monotone" dataKey="opened" name="Opened" stroke="#3b82f6" strokeWidth={2} dot={{ r: 2 }} />
                                    <Line type="monotone" dataKey="closed" name="Closed" stroke="#10b981" strokeWidth={2} dot={{ r: 2 }} />
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

                    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                        <HBarCard title="By category" rec={data.by_category} color="#8b5cf6" />
                        <ContactsCard contacts={data.top_contacts} />
                        {Object.keys(data.by_tag ?? {}).length > 0 && (
                            <HBarCard title="By tag" rec={data.by_tag} color="#ec4899" />
                        )}
                        {Object.keys(data.by_classification ?? {}).length > 0 && (
                            <HBarCard title="By classification" rec={data.by_classification} color="#0ea5e9" />
                        )}
                    </div>
                </>
            )}
        </div>
    );
}

/** Horizontal bar chart for a {label: count} map (good for long category/tag names). */
function HBarCard({ title, rec, color }: { title: string; rec: Record<string, number> | null | undefined; color: string }) {
    const data = Object.entries(rec ?? {})
        .map(([name, value]) => ({ name, value }))
        .sort((a, b) => b.value - a.value)
        .slice(0, 10);
    return (
        <Card>
            <CardHeader><CardTitle className="text-base">{title}</CardTitle></CardHeader>
            <CardContent>
                {data.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No data.</p>
                ) : (
                    <ResponsiveContainer width="100%" height={Math.max(160, data.length * 32)}>
                        <BarChart data={data} layout="vertical" margin={{ left: 20, right: 16 }}>
                            <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                            <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11 }} />
                            <YAxis type="category" dataKey="name" width={120} tick={{ fontSize: 11 }} />
                            <Tooltip />
                            <Bar dataKey="value" fill={color} />
                        </BarChart>
                    </ResponsiveContainer>
                )}
            </CardContent>
        </Card>
    );
}

function ContactsCard({ contacts }: { contacts: Array<{ name: string; count: number }> | null | undefined }) {
    const rows = contacts ?? [];
    return (
        <Card>
            <CardHeader><CardTitle className="text-base">Top requesters</CardTitle></CardHeader>
            <CardContent>
                {rows.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No data.</p>
                ) : (
                    <ResponsiveContainer width="100%" height={Math.max(160, rows.length * 32)}>
                        <BarChart data={rows} layout="vertical" margin={{ left: 20, right: 16 }}>
                            <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                            <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11 }} />
                            <YAxis type="category" dataKey="name" width={160} tick={{ fontSize: 10 }} />
                            <Tooltip />
                            <Bar dataKey="count" fill="#3b82f6" />
                        </BarChart>
                    </ResponsiveContainer>
                )}
            </CardContent>
        </Card>
    );
}

function StatCard({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
    return (
        <Card>
            <CardHeader className="pb-2">
                <CardTitle className="text-xs font-medium text-muted-foreground">{label}</CardTitle>
            </CardHeader>
            <CardContent>
                <div className={`text-2xl font-bold ${accent ? "text-blue-600" : ""}`}>{value}</div>
            </CardContent>
        </Card>
    );
}
