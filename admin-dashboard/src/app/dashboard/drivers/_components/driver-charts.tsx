"use client";

import { formatCurrency } from "@/lib/utils";
import { UserPlus, Car, DollarSign } from "lucide-react";
import {
    BarChart, Bar, LineChart, Line, XAxis, YAxis, Tooltip,
    ResponsiveContainer, CartesianGrid,
} from "recharts";

interface ChartData {
    daily_joins: { date: string; date_raw: string; count: number }[];
    daily_rides: { date: string; date_raw: string; count: number }[];
    daily_earnings: { date: string; date_raw: string; amount: number }[];
}

function ChartCard({ title, subtitle, icon: I, children }: {
    title: string; subtitle: string; icon: any; children: React.ReactNode;
}) {
    return (
        <div className="bg-card border rounded-xl p-5">
            <div className="flex items-center gap-2.5 mb-4">
                <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center">
                    <I className="h-4.5 w-4.5 text-primary" />
                </div>
                <div>
                    <h3 className="text-sm font-semibold">{title}</h3>
                    <p className="text-xs text-muted-foreground">{subtitle}</p>
                </div>
            </div>
            {children}
        </div>
    );
}

const tooltipStyle = {
    fontSize: 12,
    borderRadius: 10,
    border: '1px solid hsl(var(--border))',
    background: 'hsl(var(--card))',
    boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
};

export default function DriverCharts({ charts, loading }: { charts: ChartData | null; loading: boolean }) {
    if (loading || !charts) {
        return (
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                {[1, 2, 3].map(i => (
                    <div key={i} className="bg-card border rounded-xl p-5 h-[300px] animate-pulse">
                        <div className="flex items-center gap-2.5 mb-4">
                            <div className="w-9 h-9 rounded-lg bg-muted" />
                            <div className="space-y-1.5">
                                <div className="h-3.5 w-24 rounded bg-muted" />
                                <div className="h-2.5 w-32 rounded bg-muted" />
                            </div>
                        </div>
                        <div className="h-[200px] bg-muted rounded-lg" />
                    </div>
                ))}
            </div>
        );
    }

    return (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {/* Daily Driver Joins */}
            <ChartCard title="Driver Joins" subtitle="New driver registrations per day" icon={UserPlus}>
                <ResponsiveContainer width="100%" height={200}>
                    <BarChart data={charts.daily_joins} barSize={18}>
                        <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                        <XAxis dataKey="date" tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} />
                        <YAxis tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} allowDecimals={false} axisLine={false} tickLine={false} />
                        <Tooltip contentStyle={tooltipStyle} labelStyle={{ fontWeight: 600 }} cursor={{ fill: 'hsl(var(--muted))', radius: 4 }} />
                        <Bar dataKey="count" name="Joins" fill="hsl(var(--primary))" radius={[4, 4, 0, 0]} />
                    </BarChart>
                </ResponsiveContainer>
            </ChartCard>

            {/* Daily Rides */}
            <ChartCard title="Daily Rides" subtitle="Number of rides per day" icon={Car}>
                <ResponsiveContainer width="100%" height={200}>
                    <BarChart data={charts.daily_rides} barSize={18}>
                        <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                        <XAxis dataKey="date" tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} />
                        <YAxis tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} allowDecimals={false} axisLine={false} tickLine={false} />
                        <Tooltip contentStyle={tooltipStyle} labelStyle={{ fontWeight: 600 }} cursor={{ fill: 'hsl(var(--muted))', radius: 4 }} />
                        <Bar dataKey="count" name="Rides" fill="#8b5cf6" radius={[4, 4, 0, 0]} />
                    </BarChart>
                </ResponsiveContainer>
            </ChartCard>

            {/* Daily Earnings */}
            <ChartCard title="Driver Earnings" subtitle="Total driver earnings per day" icon={DollarSign}>
                <ResponsiveContainer width="100%" height={200}>
                    <LineChart data={charts.daily_earnings}>
                        <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                        <XAxis dataKey="date" tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} />
                        <YAxis tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false}
                            tickFormatter={(v) => `$${v}`} />
                        <Tooltip contentStyle={tooltipStyle} labelStyle={{ fontWeight: 600 }}
                            formatter={(value: any) => [formatCurrency(Number(value || 0)), "Earnings"]} />
                        <Line type="monotone" dataKey="amount" name="Earnings" stroke="#10b981" strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} />
                    </LineChart>
                </ResponsiveContainer>
            </ChartCard>
        </div>
    );
}
