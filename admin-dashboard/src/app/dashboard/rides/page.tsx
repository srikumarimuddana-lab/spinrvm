"use client";

import { useEffect, useState } from "react";
import { getRides, getServiceAreas } from "@/lib/api";
import { exportToCsv } from "@/lib/export-csv";
import { formatCurrency } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import {
    Car, Search, Clock, CheckCircle, XCircle, MapPin, Loader,
    Download, ChevronRight, X, DollarSign, User, Phone, Mail,
    Star, Route, Ticket, Receipt, Send, Percent,
} from "lucide-react";

const STATUS_TABS = [
    { value: "all", label: "All", icon: Car },
    { value: "searching", label: "Searching", icon: Loader },
    { value: "driver_assigned", label: "Assigned", icon: MapPin },
    { value: "in_progress", label: "In Progress", icon: Clock },
    { value: "completed", label: "Completed", icon: CheckCircle },
    { value: "cancelled", label: "Cancelled", icon: XCircle },
];

export default function RidesPage() {
    const [rides, setRides] = useState<any[]>([]);
    const [areas, setAreas] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState("all");
    const [areaFilter, setAreaFilter] = useState("all");
    const [selected, setSelected] = useState<any>(null);

    useEffect(() => {
        Promise.all([getRides(), getServiceAreas().catch(() => [])])
            .then(([r, a]) => { setRides(r); setAreas(a); })
            .catch(() => {}).finally(() => setLoading(false));
    }, []);

    const filtered = rides.filter(r => {
        const q = search.toLowerCase();
        const matchSearch = !search ||
            r.pickup_address?.toLowerCase().includes(q) ||
            r.dropoff_address?.toLowerCase().includes(q) ||
            r.id?.toLowerCase().includes(q) ||
            r.rider_name?.toLowerCase().includes(q) ||
            r.rider_phone?.toLowerCase().includes(q) ||
            r.driver_name?.toLowerCase().includes(q) ||
            r.driver_phone?.toLowerCase().includes(q) ||
            r.rider_id?.toLowerCase().includes(q) ||
            r.driver_id?.toLowerCase().includes(q);
        const matchStatus = statusFilter === "all" || r.status === statusFilter;
        const matchArea = areaFilter === "all" || r.service_area_id === areaFilter;
        return matchSearch && matchStatus && matchArea;
    });

    const statusCounts = (s: string) => s === "all" ? rides.length : rides.filter(r => r.status === s).length;
    const fmtTime = (d: string) => { if (!d) return "—"; try { return new Date(d).toLocaleString("en-CA", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }); } catch { return d; } };

    const getStatusBadge = (status: string) => {
        const map: Record<string, string> = {
            completed: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
            cancelled: "bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-400",
            in_progress: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
            searching: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
            driver_assigned: "bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-400",
            driver_arrived: "bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-400",
        };
        return <span className={`px-2 py-0.5 rounded-md text-xs font-bold ${map[status] || "bg-gray-100 text-gray-600"}`}>{status?.replace(/_/g, " ").toUpperCase()}</span>;
    };

    const handleSendInvoice = async (rideId: string) => {
        try {
            const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
            const token = (await import("@/store/authStore")).useAuthStore.getState().token;
            await fetch(`${API_BASE}/api/v1/rides/${rideId}/process-payment`, {
                method: "POST", headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}` },
                body: JSON.stringify({ tip_amount: 0 }),
            });
            alert("Invoice/receipt sent to rider's email");
        } catch { alert("Failed to send invoice"); }
    };

    return (
        <div className="flex gap-4 h-[calc(100vh-100px)]">
            {/* Left */}
            <div className={`flex flex-col ${selected ? "w-1/2 lg:w-3/5" : "w-full"} transition-all`}>
                <div className="flex items-center justify-between mb-4">
                    <div>
                        <h1 className="text-2xl font-bold">Rides</h1>
                        <p className="text-sm text-muted-foreground">{filtered.length} of {rides.length} rides</p>
                    </div>
                    <button onClick={() => exportToCsv("rides", filtered, [
                        {key:"id",label:"ID"},{key:"pickup_address",label:"Pickup"},{key:"dropoff_address",label:"Dropoff"},
                        {key:"status",label:"Status"},{key:"total_fare",label:"Fare"},{key:"tip_amount",label:"Tip"},
                        {key:"distance_km",label:"km"},{key:"duration_minutes",label:"min"},{key:"created_at",label:"Date"},
                    ])}
                        className="flex items-center gap-2 text-sm font-medium text-muted-foreground hover:text-foreground px-3 py-1.5 rounded-lg hover:bg-muted">
                        <Download className="h-4 w-4" /> Export
                    </button>
                </div>

                <div className="flex gap-2 mb-3 flex-wrap">
                    <div className="flex gap-1 overflow-x-auto">
                        {STATUS_TABS.map(tab => (
                            <button key={tab.value} onClick={() => setStatusFilter(tab.value)}
                                className={`flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition ${
                                    statusFilter === tab.value ? "bg-primary text-white" : "bg-muted text-muted-foreground"
                                }`}>
                                <tab.icon className="h-3 w-3" /> {tab.label}
                                <span className={`ml-0.5 px-1 rounded text-[10px] ${statusFilter === tab.value ? "bg-white/20" : "bg-background"}`}>{statusCounts(tab.value)}</span>
                            </button>
                        ))}
                    </div>
                    <select value={areaFilter} onChange={e => setAreaFilter(e.target.value)}
                        className="text-xs border rounded-lg px-2 py-1.5 bg-card text-foreground">
                        <option value="all">All Areas</option>
                        {areas.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
                    </select>
                </div>

                <div className="relative mb-3">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input placeholder="Search rider/driver name, phone, address, ID..." value={search} onChange={e => setSearch(e.target.value)} className="pl-9 h-9 text-sm" />
                </div>

                <div className="flex-1 overflow-y-auto space-y-2 pr-1">
                    {loading ? (
                        <div className="flex items-center justify-center py-20"><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
                    ) : filtered.length === 0 ? (
                        <div className="text-center py-16 text-muted-foreground"><Car className="h-10 w-10 mx-auto mb-3 opacity-30" /><p>No rides found</p></div>
                    ) : filtered.map(ride => (
                        <div key={ride.id} onClick={() => setSelected(ride)}
                            className={`flex items-center gap-3 p-3 rounded-xl border cursor-pointer transition-all hover:shadow-sm ${selected?.id === ride.id ? "border-primary bg-primary/5" : "border-border"}`}>
                            {getStatusBadge(ride.status)}
                            <div className="flex-1 min-w-0">
                                <p className="text-sm font-medium truncate">{ride.pickup_address || "—"}</p>
                                <p className="text-xs text-muted-foreground truncate">→ {ride.dropoff_address || "—"}</p>
                            </div>
                            <div className="text-right shrink-0">
                                <p className="text-sm font-bold">{formatCurrency(ride.total_fare || 0)}</p>
                                <p className="text-[10px] text-muted-foreground">{fmtTime(ride.created_at)}</p>
                            </div>
                            <ChevronRight className="h-4 w-4 text-muted-foreground/40 shrink-0" />
                        </div>
                    ))}
                </div>
            </div>

            {/* Right: Detail */}
            {selected && (
                <div className="w-1/2 lg:w-2/5 bg-card border rounded-2xl overflow-y-auto">
                    <div className="flex items-center justify-between p-4 border-b sticky top-0 bg-card z-10">
                        <div>
                            <p className="text-[10px] text-muted-foreground font-mono">{selected.id}</p>
                            <div className="mt-1">{getStatusBadge(selected.status)}</div>
                        </div>
                        <div className="flex items-center gap-2">
                            {selected.status === "completed" && (
                                <button onClick={() => handleSendInvoice(selected.id)} className="flex items-center gap-1 text-xs font-semibold text-primary hover:bg-primary/10 px-2.5 py-1.5 rounded-lg"><Send className="h-3.5 w-3.5" /> Invoice</button>
                            )}
                            <button onClick={() => setSelected(null)} className="p-1.5 hover:bg-muted rounded-lg"><X className="h-4 w-4" /></button>
                        </div>
                    </div>

                    <div className="p-4 space-y-4">
                        {/* Route */}
                        <Sec title="Route">
                            <div className="flex gap-3">
                                <div className="flex flex-col items-center pt-1"><div className="w-2.5 h-2.5 rounded-full bg-emerald-500" /><div className="w-0.5 flex-1 bg-border my-1" /><div className="w-2.5 h-2.5 rounded-full bg-primary" /></div>
                                <div className="flex-1">
                                    <p className="text-[10px] text-muted-foreground uppercase font-bold">Pickup</p>
                                    <p className="text-sm font-medium">{selected.pickup_address || "—"}</p>
                                    <div className="h-3" />
                                    <p className="text-[10px] text-muted-foreground uppercase font-bold">Dropoff</p>
                                    <p className="text-sm font-medium">{selected.dropoff_address || "—"}</p>
                                </div>
                            </div>
                            <div className="flex gap-2 mt-3 pt-3 border-t">
                                <MStat label="Distance" value={`${(selected.distance_km||0).toFixed(1)} km`} icon={Route} />
                                <MStat label="Duration" value={`${selected.duration_minutes||0} min`} icon={Clock} />
                                <MStat label="Surge" value={`${selected.surge_multiplier||1.0}x`} icon={Percent} />
                            </div>
                        </Sec>

                        {/* Rider */}
                        <Sec title="Rider">
                            <div className="flex items-center gap-3">
                                <div className="w-9 h-9 rounded-full bg-blue-100 dark:bg-blue-900/30 flex items-center justify-center shrink-0"><User className="h-4 w-4 text-blue-600 dark:text-blue-400" /></div>
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-semibold">{selected.rider_name || selected.rider_id?.slice(0,12) || "—"}</p>
                                    <div className="flex items-center gap-3 text-xs text-muted-foreground mt-0.5">
                                        {selected.rider_phone && <span className="flex items-center gap-1"><Phone className="h-3 w-3" />{selected.rider_phone}</span>}
                                        {selected.rider_email && <span className="flex items-center gap-1"><Mail className="h-3 w-3" />{selected.rider_email}</span>}
                                    </div>
                                </div>
                                {selected.rider_rating && <div className="flex items-center gap-0.5"><Star className="h-3.5 w-3.5 text-amber-400" /><span className="text-xs font-bold">{selected.rider_rating}</span></div>}
                            </div>
                        </Sec>

                        {/* Driver */}
                        <Sec title="Driver">
                            {selected.driver_id ? (
                                <div className="flex items-center gap-3">
                                    <div className="w-9 h-9 rounded-full bg-emerald-100 dark:bg-emerald-900/30 flex items-center justify-center shrink-0"><Car className="h-4 w-4 text-emerald-600 dark:text-emerald-400" /></div>
                                    <div className="flex-1 min-w-0">
                                        <p className="text-sm font-semibold">{selected.driver_name || selected.driver_id?.slice(0,12)}</p>
                                        <div className="flex items-center gap-3 text-xs text-muted-foreground mt-0.5">
                                            {selected.driver_phone && <span className="flex items-center gap-1"><Phone className="h-3 w-3" />{selected.driver_phone}</span>}
                                            <span>{selected.driver_vehicle || "—"}</span>
                                            {selected.driver_plate && <span className="font-mono font-bold text-foreground">{selected.driver_plate}</span>}
                                        </div>
                                    </div>
                                </div>
                            ) : <p className="text-sm text-muted-foreground">No driver assigned</p>}
                        </Sec>

                        {/* Fare */}
                        <Sec title="Fare Breakdown">
                            <FR l="Base fare" v={selected.base_fare} /><FR l={`Distance (${(selected.distance_km||0).toFixed(1)} km)`} v={selected.distance_fare} />
                            <FR l={`Time (${selected.duration_minutes||0} min)`} v={selected.time_fare} /><FR l="Booking fee" v={selected.booking_fee} />
                            {(selected.airport_fee||0)>0 && <FR l="Airport fee" v={selected.airport_fee} />}
                            <div className="border-t my-2" /><FR l="Subtotal" v={selected.total_fare} b />
                        </Sec>

                        {/* Promo & Tip */}
                        {((selected.tip_amount||0)>0 || selected.promo_code) && (
                            <Sec title="Extras">
                                {selected.promo_code && <div className="flex justify-between"><span className="flex items-center gap-2 text-sm"><Ticket className="h-4 w-4 text-violet-500" />Promo: <b className="font-mono">{selected.promo_code}</b></span><span className="text-sm font-semibold text-emerald-600">-{formatCurrency(selected.promo_discount||0)}</span></div>}
                                {(selected.tip_amount||0)>0 && <div className="flex justify-between"><span className="flex items-center gap-2 text-sm"><DollarSign className="h-4 w-4 text-amber-500" />Tip</span><span className="text-sm font-semibold text-amber-600">{formatCurrency(selected.tip_amount)}</span></div>}
                            </Sec>
                        )}

                        {/* Revenue Split */}
                        <Sec title="Revenue Split">
                            <div className="grid grid-cols-3 gap-2">
                                <div className="bg-emerald-50 dark:bg-emerald-900/20 rounded-xl p-3 text-center">
                                    <p className="text-lg font-extrabold text-emerald-600">{formatCurrency(selected.driver_earnings||0)}</p>
                                    <p className="text-[10px] text-muted-foreground mt-1">Driver</p>
                                </div>
                                <div className="bg-violet-50 dark:bg-violet-900/20 rounded-xl p-3 text-center">
                                    <p className="text-lg font-extrabold text-violet-600">{formatCurrency(selected.admin_earnings||0)}</p>
                                    <p className="text-[10px] text-muted-foreground mt-1">Platform</p>
                                </div>
                                <div className="bg-amber-50 dark:bg-amber-900/20 rounded-xl p-3 text-center">
                                    <p className="text-lg font-extrabold text-amber-600">{formatCurrency(selected.tip_amount||0)}</p>
                                    <p className="text-[10px] text-muted-foreground mt-1">Tip</p>
                                </div>
                            </div>
                            <div className="flex justify-between mt-2 pt-2 border-t">
                                <span className="text-sm font-bold">Total Charged</span>
                                <span className="text-lg font-extrabold text-primary">{formatCurrency((selected.total_fare||0)+(selected.tip_amount||0)-(selected.promo_discount||0))}</span>
                            </div>
                        </Sec>

                        {/* Payment */}
                        <Sec title="Payment">
                            <div className="flex items-center justify-between">
                                <span className="flex items-center gap-2 text-sm"><Receipt className="h-4 w-4 text-muted-foreground" />{selected.payment_method||"Card"}</span>
                                <span className={`text-xs font-bold px-2 py-0.5 rounded ${selected.payment_status==="paid"?"bg-emerald-100 text-emerald-700":"bg-amber-100 text-amber-700"}`}>{(selected.payment_status||"pending").toUpperCase()}</span>
                            </div>
                        </Sec>

                        {/* Cancel */}
                        {selected.status==="cancelled" && (
                            <Sec title="Cancellation">
                                <FR l="Reason" v={selected.cancellation_reason||"—"} t /><FR l="Driver fee" v={selected.cancellation_fee_driver} /><FR l="Admin fee" v={selected.cancellation_fee_admin} />
                            </Sec>
                        )}

                        {/* Timeline */}
                        <Sec title="Timeline">
                            <TL l="Requested" t={selected.ride_requested_at||selected.created_at} /><TL l="Driver notified" t={selected.driver_notified_at} />
                            <TL l="Driver accepted" t={selected.driver_accepted_at} /><TL l="Driver arrived" t={selected.driver_arrived_at} />
                            <TL l="Ride started" t={selected.ride_started_at} /><TL l="Ride completed" t={selected.ride_completed_at} />
                            {selected.cancelled_at && <TL l="Cancelled" t={selected.cancelled_at} d />}
                        </Sec>

                        <div className="text-[11px] text-muted-foreground space-y-1 pt-2 border-t">
                            <p>Vehicle type: {selected.vehicle_type_id?.slice(0,8)||"—"}</p><p>OTP: {selected.pickup_otp||"—"}</p>
                            {selected.rider_comment && <p>Comment: "{selected.rider_comment}"</p>}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

function Sec({title,children}:{title:string;children:React.ReactNode}){return<div><h4 className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider mb-2">{title}</h4><div className="bg-muted/30 rounded-xl p-3 space-y-2">{children}</div></div>}
function FR({l,v,b,t}:{l:string;v?:any;b?:boolean;t?:boolean}){const d=t?(v||"—"):formatCurrency(v||0);return<div className="flex justify-between text-sm"><span className="text-muted-foreground">{l}</span><span className={b?"font-bold":"font-medium"}>{d}</span></div>}
function MStat({label,value,icon:I}:{label:string;value:string;icon:any}){return<div className="flex-1 bg-background rounded-lg p-2 text-center"><I className="h-3.5 w-3.5 text-muted-foreground mx-auto mb-1" /><p className="text-xs font-bold">{value}</p><p className="text-[9px] text-muted-foreground">{label}</p></div>}
function TL({l,t,d}:{l:string;t?:string;d?:boolean}){if(!t)return null;return<div className="flex items-center gap-2.5"><div className={`w-1.5 h-1.5 rounded-full shrink-0 ${d?"bg-red-400":"bg-emerald-400"}`}/><p className="text-sm flex-1">{l}</p><p className="text-[10px] text-muted-foreground">{new Date(t).toLocaleString("en-CA",{month:"short",day:"numeric",hour:"numeric",minute:"2-digit"})}</p></div>}
