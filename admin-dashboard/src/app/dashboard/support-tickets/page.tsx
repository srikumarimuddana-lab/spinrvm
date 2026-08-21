"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getZohoConfig, getDeskDashboard, syncDeskTickets, ZohoDashboard, ZohoConfigStatus } from "@/lib/api";
import { useRequireModule } from "@/hooks/useRequireModule";
import { useToast } from "@/components/ui/use-toast";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ZohoConfigCard } from "./_components/zoho-config-card";
import { Headphones, Inbox, BarChart3, Settings as SettingsIcon, AlertCircle, Info, RefreshCw, Ticket as TicketIcon } from "lucide-react";

function timeAgo(iso?: string | null): string {
    if (!iso) return "never";
    const ms = Date.now() - new Date(iso).getTime();
    if (!isFinite(ms) || ms < 0) return "just now";
    const m = Math.floor(ms / 60000);
    if (m < 1) return "just now";
    if (m < 60) return `${m} min ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    return `${Math.floor(h / 24)}d ago`;
}

function statusClass(status: string): string {
    const s = (status || "").toLowerCase();
    // eslint-disable-next-line no-restricted-syntax -- "open" (new/active) has no semantic-token equivalent; must stay visually distinct from hold/escalated/closed (#2816)
    if (s.includes("open")) return "bg-blue-100 text-blue-800 hover:bg-blue-100";
    if (s.includes("hold")) return "bg-warning/15 text-warning hover:bg-warning/15";
    if (s.includes("escal")) return "bg-destructive/15 text-destructive hover:bg-destructive/15";
    if (s.includes("closed")) return "bg-muted text-muted-foreground hover:bg-muted";
    return "bg-muted text-muted-foreground hover:bg-muted";
}

export default function HelpDeskPage() {
    const { allowed } = useRequireModule("support_tickets");
    const { toast } = useToast();
    const [connected, setConnected] = useState<boolean | null>(null);
    const [cfg, setCfg] = useState<ZohoConfigStatus | null>(null);
    const [data, setData] = useState<ZohoDashboard | null>(null);
    const [loading, setLoading] = useState(true);
    const [syncing, setSyncing] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [showSettings, setShowSettings] = useState(false);

    const loadDashboard = async () => {
        setLoading(true);
        setError(null);
        try {
            const c = await getZohoConfig();
            setCfg(c);
            setConnected(c.connected);
            if (c.connected) {
                setData(await getDeskDashboard());
            }
        } catch (e: any) {
            setError(e?.message || "Failed to load");
        } finally {
            setLoading(false);
        }
    };

    const runSync = async () => {
        setSyncing(true);
        try {
            const r = await syncDeskTickets();
            toast({
                title: r.skipped ? "Sync skipped" : "Synced",
                description: r.skipped ? r.skipped : `${r.upserted ?? 0} tickets updated`,
            });
            await loadDashboard();
        } catch (e: any) {
            toast({ title: "Sync failed", description: e?.message, variant: "destructive" });
        } finally {
            setSyncing(false);
        }
    };

    useEffect(() => {
        if (allowed) loadDashboard();
    }, [allowed]);

    if (!allowed) return null;

    // Show the config form when the admin opens Settings, OR whenever we know
    // the integration isn't connected (including when the config call errored,
    // so the form is always reachable to set things up / fix credentials).
    const showConfig = showSettings || connected !== true;

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="flex items-center gap-2 text-2xl font-bold">
                        <Headphones className="h-6 w-6" /> Help Desk
                    </h1>
                    <p className="text-sm text-muted-foreground">Zoho Desk support tickets</p>
                </div>
                <div className="flex items-center gap-2">
                    {connected && (
                        <Button variant="outline" size="sm" onClick={runSync} disabled={syncing} title={`Last synced ${timeAgo(cfg?.last_synced_at)}`}>
                            <RefreshCw className={`mr-2 h-4 w-4 ${syncing ? "animate-spin" : ""}`} />
                            {syncing ? "Syncing…" : `Synced ${timeAgo(cfg?.last_synced_at)}`}
                        </Button>
                    )}
                    <Button asChild variant="outline">
                        <Link href="/dashboard/support-tickets/tickets"><Inbox className="mr-2 h-4 w-4" /> Tickets</Link>
                    </Button>
                    <Button asChild variant="outline">
                        <Link href="/dashboard/support-tickets/trends"><BarChart3 className="mr-2 h-4 w-4" /> Trends</Link>
                    </Button>
                    <Button variant="ghost" onClick={() => setShowSettings((v) => !v)}>
                        <SettingsIcon className="mr-2 h-4 w-4" /> Settings
                    </Button>
                </div>
            </div>

            {loading && <Card><CardContent className="p-6 text-muted-foreground">Loading…</CardContent></Card>}

            {/* Surface any load error regardless of connection state, so a missing
                table / expired token / network failure is visible instead of a
                blank page. */}
            {!loading && error && (
                <Card><CardContent className="p-4 text-sm text-destructive">{error}</CardContent></Card>
            )}

            {!loading && connected === false && (
                <Card>
                    <CardContent className="flex items-center gap-3 p-4 text-sm text-warning">
                        <AlertCircle className="h-5 w-5" />
                        Zoho Desk is not connected yet. Add your credentials below to enable the Help Desk.
                    </CardContent>
                </Card>
            )}

            {!loading && showConfig && <ZohoConfigCard onSaved={() => loadDashboard()} />}

            {!loading && connected && data && (
                <>
                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                        {/* Only `total` is an exact account-wide count. The open/status
                            figures are derived from the latest sample, so label them
                            as such rather than presenting them as account-wide totals. */}
                        <StatCard
                            label="Total tickets"
                            value={data.total}
                            sub={!data.total_available ? "needs Desk.search.READ scope" : undefined}
                        />
                        <StatCard
                            label="Open / active"
                            value={data.open}
                            accent
                            sub={data.approximate ? `of latest ${data.sample_size}` : undefined}
                        />
                        {Object.entries(data.by_status).slice(0, 2).map(([k, v]) => (
                            <StatCard
                                key={k}
                                label={k}
                                value={v}
                                sub={data.approximate ? `of latest ${data.sample_size}` : undefined}
                            />
                        ))}
                    </div>

                    <Card>
                        <CardHeader className="pb-2">
                            <CardTitle className="flex items-center gap-2 text-base">
                                Status breakdown
                                {data.approximate && (
                                    <span className="flex items-center gap-1 text-xs font-normal text-muted-foreground">
                                        <Info className="h-3 w-3" /> latest {data.sample_size} tickets (sample)
                                    </span>
                                )}
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                            {Object.entries(data.by_status).map(([k, v]) => (
                                <div key={k} className="flex items-center justify-between rounded-lg border p-4">
                                    <Badge variant="secondary" className={statusClass(k)}>{k}</Badge>
                                    <span className="text-xl font-semibold">{v}</span>
                                </div>
                            ))}
                        </CardContent>
                    </Card>

                    <Card>
                        <CardHeader><CardTitle className="text-base">Recent tickets</CardTitle></CardHeader>
                        <CardContent className="space-y-2">
                            {data.recent.length === 0 && (
                                <p className="text-sm text-muted-foreground">No recent tickets.</p>
                            )}
                            {data.recent.map((t: any) => (
                                <Link
                                    key={t.id}
                                    href={`/dashboard/support-tickets/tickets/${t.id}`}
                                    className="flex items-center justify-between rounded-lg border p-3 hover:bg-muted/50"
                                >
                                    <div className="flex min-w-0 items-center gap-3">
                                        <TicketIcon className="h-4 w-4 shrink-0 text-muted-foreground" />
                                        <div className="min-w-0">
                                            <p className="truncate font-medium">{t.subject || "(no subject)"}</p>
                                            <p className="truncate text-xs text-muted-foreground">
                                                #{t.ticketNumber} · {t.contact?.email || t.email || "—"}
                                            </p>
                                        </div>
                                    </div>
                                    <Badge variant="secondary" className={statusClass(t.status)}>{t.status}</Badge>
                                </Link>
                            ))}
                        </CardContent>
                    </Card>
                </>
            )}
        </div>
    );
}

function StatCard({ label, value, accent, sub }: { label: string; value: number | null; accent?: boolean; sub?: string }) {
    return (
        <Card>
            <CardHeader className="pb-2"><CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle></CardHeader>
            <CardContent>
                <div className={`text-2xl font-bold ${accent ? "text-blue-600" : ""}`}>{value ?? "—"}</div>
                {sub && <p className="mt-1 text-xs text-muted-foreground">{sub}</p>}
            </CardContent>
        </Card>
    );
}
