// Overview tab of the drivers detail slideout. Pure code motion out of
// drivers/page.tsx (design-audit follow-up, PR #4955's un-extracted
// remainder) -- no logic changes. Controlled/presentational: every piece of
// state it reads is still owned by DriversPage and passed down as props,
// matching driver-payouts-tab.tsx / driver-rides-tab.tsx. `selected`/driver
// fields stay `any` to match every sibling tab component (driver-action-bar,
// driver-payouts-tab, etc.) -- the drivers row shape is used dynamically
// across ~30 optional/legacy/computed fields and there is no shared Driver
// type in lib/api.ts today; inventing one here would be a wider,
// higher-risk change than this pure-code-motion PR, and out of this PR's
// drivers/-only scope. Callback and primitive props ARE given real types.
import { formatCurrency } from "@/lib/utils";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Star, Car, MapPin, CreditCard, Clock, DollarSign, CheckCircle, XCircle, FileText, Phone, Mail, CalendarRange, ShieldCheck, Shield, Trash2 } from "lucide-react";
import { maskEmail, maskPhone, maskPlate, maskVin } from "@/lib/pii";
import type { DriverLiveStats } from "@/lib/api";
import { DetailSection, DetailField, CopyableField, EditField, EditBooleanField, workAuth, WORK_AUTH_LABELS, WORK_AUTH_FLAG_LABELS } from "./driver-detail-shared";

interface DriverOverviewTabProps {
    selected: any;
    editing: boolean;
    ef: (field: string) => string;
    setEf: (field: string, value: string) => void;
    allServiceAreas: any[];
    vehicleTypes: { id: string; name: string }[];
    vehicleTypesByArea: Record<string, Set<string>>;
    serviceAreas: { id: string; name: string }[];
    showPii: boolean;
    liveStats: DriverLiveStats | null;
    vehicleHistory: any[];
    themeV2Enabled: boolean;
    fmtDate: (d: string) => string;
}

export default function DriverOverviewTab({
    selected,
    editing,
    ef,
    setEf,
    allServiceAreas,
    vehicleTypes,
    vehicleTypesByArea,
    serviceAreas,
    showPii,
    liveStats,
    vehicleHistory,
    themeV2Enabled,
    fmtDate,
}: DriverOverviewTabProps) {
    return (
        <>
                                    <DetailSection title="Performance" icon={Star}>
                                        {liveStats ? (
                                            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5">
                                                <DetailField icon={Car} label="Total Assigned" value={(liveStats.total_assigned ?? 0).toLocaleString()} />
                                                <DetailField icon={CheckCircle} label="Completed" value={(liveStats.total_rides ?? 0).toLocaleString()} />
                                                <DetailField icon={ShieldCheck} label="Acceptance Rate" value={liveStats.acceptance_rate != null ? `${liveStats.acceptance_rate}%` : "—"} />
                                                <DetailField icon={XCircle} label="Cancelled (driver)" value={(liveStats.cancelled_by_driver ?? 0).toLocaleString()} />
                                                <DetailField icon={Star} label="Avg Rating" value={liveStats.avg_rating != null ? liveStats.avg_rating.toFixed(2) : "—"} />
                                                <DetailField icon={DollarSign} label="Avg / Ride" value={liveStats.total_rides > 0 ? formatCurrency(liveStats.total_earnings / liveStats.total_rides) : "—"} />
                                            </div>
                                        ) : (
                                            <p className="text-xs text-muted-foreground">Loading stats…</p>
                                        )}
                                    </DetailSection>
                                    <DetailSection title="Contact Information" icon={Mail}>
                                        {editing ? (
                                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                                                <EditField label="First Name" value={ef("first_name")} onChange={v => setEf("first_name", v)} />
                                                <EditField label="Last Name" value={ef("last_name")} onChange={v => setEf("last_name", v)} />
                                                <EditField label="Email" value={ef("email")} onChange={v => setEf("email", v)} type="email" />
                                                <EditField label="Phone" value={ef("phone")} onChange={v => setEf("phone", v)} type="tel" />
                                                <EditField label="City" value={ef("city")} onChange={v => setEf("city", v)} />
                                                <div><label className="text-[11px] text-muted-foreground mb-1 block">Service Area</label><Select value={ef("service_area_id") || "none"} onValueChange={v => setEf("service_area_id", v === "none" ? "" : v)}><SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="none">Not assigned</SelectItem>{allServiceAreas.map(a => <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>)}</SelectContent></Select></div>
                                            </div>
                                        ) : (
                                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                                                <CopyableField icon={Mail} label="Email" value={showPii ? selected.email : maskEmail(selected.email)} />
                                                <CopyableField icon={Phone} label="Phone" value={showPii ? selected.phone : maskPhone(selected.phone)} />
                                                <DetailField icon={MapPin} label="City" value={selected.city || "\u2014"} />
                                                <DetailField icon={MapPin} label="Service Area" value={serviceAreas.find(a => a.id === selected.service_area_id)?.name || selected.service_area_id?.slice(0, 8) || "Not assigned"} />
                                            </div>
                                        )}
                                    </DetailSection>
                                    <DetailSection title="Vehicle Information" icon={Car}>
                                        {editing ? (() => {
                                            // Narrow vehicle-type options to the types with
                                            // active fare_configs for the currently-selected
                                            // service area — same convention the monitoring
                                            // filter uses. No area selected → show every
                                            // active type.
                                            const areaId = ef("service_area_id");
                                            const allowed = areaId ? vehicleTypesByArea[areaId] : null;
                                            // The dropdown is NEVER disabled — admins must
                                            // be able to recover any driver from a bad
                                            // state (vehicle type deleted, area
                                            // mis-configured, etc.). When the area has no
                                            // active fare_configs we still show every
                                            // catalogue type and warn inline so the
                                            // operator knows configs are missing.
                                            const areaHasConfigs = !!allowed && allowed.size > 0;
                                            const availableTypes = areaHasConfigs
                                                ? vehicleTypes.filter(v => allowed!.has(v.id))
                                                : vehicleTypes;
                                            const currentTypeId = ef("vehicle_type_id");
                                            const currentInList = availableTypes.some(v => v.id === currentTypeId);
                                            return (
                                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                                                    <div>
                                                        <label className="text-[11px] text-muted-foreground mb-1 block">Vehicle Type</label>
                                                        <Select
                                                            value={currentTypeId || "none"}
                                                            onValueChange={v => setEf("vehicle_type_id", v === "none" ? "" : v)}
                                                        >
                                                            <SelectTrigger className="h-9 text-sm">
                                                                <SelectValue placeholder="Select vehicle type" />
                                                            </SelectTrigger>
                                                            <SelectContent>
                                                                <SelectItem value="none">Not assigned</SelectItem>
                                                                {availableTypes.map(v => (
                                                                    <SelectItem key={v.id} value={v.id}>{v.name}</SelectItem>
                                                                ))}
                                                                {currentTypeId && !currentInList && (
                                                                    <SelectItem value={currentTypeId}>
                                                                        {vehicleTypes.find(v => v.id === currentTypeId)?.name || currentTypeId.slice(0, 8)} (deleted)
                                                                    </SelectItem>
                                                                )}
                                                            </SelectContent>
                                                        </Select>
                                                        {areaId && !areaHasConfigs && (
                                                            <p className="text-[10px] text-warning mt-1">
                                                                No fare configs for this area — set them up in Service Areas → Vehicle Pricing.
                                                            </p>
                                                        )}
                                                    </div>
                                                    <EditField label="Make" value={ef("vehicle_make")} onChange={v => setEf("vehicle_make", v)} />
                                                    <EditField label="Model" value={ef("vehicle_model")} onChange={v => setEf("vehicle_model", v)} />
                                                    <EditField label="Color" value={ef("vehicle_color")} onChange={v => setEf("vehicle_color", v)} />
                                                    <EditField label="Year" value={ef("vehicle_year")} onChange={v => setEf("vehicle_year", v)} />
                                                    <EditField label="License Plate" value={ef("license_plate")} onChange={v => setEf("license_plate", v)} />
                                                    <EditField label="VIN" value={ef("vehicle_vin")} onChange={v => setEf("vehicle_vin", v)} />
                                                </div>
                                            );
                                        })() : (
                                            <>
                                                <div className="grid grid-cols-2 gap-2.5">
                                                    <DetailField icon={Car} label="Vehicle Type" value={vehicleTypes.find(v => v.id === selected.vehicle_type_id)?.name || (selected.vehicle_type_id ? selected.vehicle_type_id.slice(0, 8) : "Not assigned")} />
                                                    <DetailField icon={CalendarRange} label="Year" value={selected.vehicle_year ? String(selected.vehicle_year) : "\u2014"} />
                                                    <DetailField icon={Car} label="Make" value={selected.vehicle_make || "\u2014"} />
                                                    <DetailField icon={Car} label="Model" value={selected.vehicle_model || "\u2014"} />
                                                    <DetailField icon={Car} label="Color" value={selected.vehicle_color || "\u2014"} />
                                                    <DetailField icon={FileText} label="License Plate" value={showPii ? (selected.license_plate || "\u2014") : maskPlate(selected.license_plate)} mono />
                                                </div>
                                                <div className="mt-2.5">
                                                    <DetailField icon={FileText} label="VIN" value={showPii ? (selected.vehicle_vin || "\u2014") : maskVin(selected.vehicle_vin)} mono />
                                                </div>
                                            </>
                                        )}
                                    </DetailSection>
                                    <DetailSection title="Compliance & Import Data" icon={ShieldCheck}>
                                        {editing ? (
                                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                                                <EditField label="Date of Birth" value={ef("date_of_birth")} onChange={v => setEf("date_of_birth", v)} type="date" />
                                                <EditField
                                                    label="License Number"
                                                    value={ef("license_number")}
                                                    onChange={v => setEf("license_number", v)}
                                                    placeholder={liveStats?.license_number_last4 ? `•••• ${liveStats.license_number_last4}` : "Not on file"}
                                                    hint="Leave blank to keep the number on file. Stored encrypted; only the last 4 are ever displayed."
                                                />
                                                <EditField label="License Class" value={ef("license_class")} onChange={v => setEf("license_class", v)} />
                                                <EditField label="Regulatory Authority" value={ef("regulatory_authority")} onChange={v => setEf("regulatory_authority", v)} />
                                                <EditField label="Regulatory Region" value={ef("regulatory_region")} onChange={v => setEf("regulatory_region", v)} />
                                                <EditBooleanField label="Authority Approved" value={ef("regulatory_authority_approved")} onChange={v => setEf("regulatory_authority_approved", v)} />
                                                <EditField label="Authority Approved At" value={ef("regulatory_authority_approved_at")} onChange={v => setEf("regulatory_authority_approved_at", v)} type="datetime-local" />
                                                <div className="sm:col-span-2">
                                                    <label className="text-[11px] text-muted-foreground mb-1 block">Work Authorization</label>
                                                    <Select value={ef("work_authorization_status") || "unknown"} onValueChange={v => setEf("work_authorization_status", v === "unknown" ? "" : v)}>
                                                        <SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger>
                                                        <SelectContent>
                                                            {Object.entries(WORK_AUTH_LABELS).map(([value, label]) => (
                                                                <SelectItem key={value} value={value}>{label}</SelectItem>
                                                            ))}
                                                        </SelectContent>
                                                    </Select>
                                                    <p className="text-[10px] text-muted-foreground mt-1">
                                                        Citizen / permanent resident / work permit are mutually exclusive — picking one marks the others not applicable.
                                                    </p>
                                                </div>
                                                <EditField label="Welcome Letter Ref #" value={ef("decal_number")} onChange={v => setEf("decal_number", v)} />
                                                <EditField label="Welcome Letter Generated" value={ef("decal_generated_at")} onChange={v => setEf("decal_generated_at", v)} type="datetime-local" />
                                                <EditBooleanField label="Welcome Letter Sent" value={ef("decals_sent")} onChange={v => setEf("decals_sent", v)} />
                                                <EditField label="Welcome Letter Sent At" value={ef("decals_sent_at")} onChange={v => setEf("decals_sent_at", v)} type="datetime-local" />
                                            </div>
                                        ) : (
                                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                                                <DetailField icon={CalendarRange} label="Date of Birth" value={selected.date_of_birth ? fmtDate(selected.date_of_birth) : "—"} />
                                                <DetailField
                                                    icon={FileText}
                                                    label="License Number"
                                                    value={liveStats?.license_number_last4 ? `•••• ${liveStats.license_number_last4}` : liveStats?.license_number_on_file ? "On file (unreadable)" : "—"}
                                                    mono
                                                />
                                                <DetailField
                                                    icon={ShieldCheck}
                                                    label="SIN (T4A)"
                                                    // Masked always. The full number comes only from the
                                                    // audited super_admin reveal, never from this panel.
                                                    // "Missing" is called out because a driver without one
                                                    // cannot be filed for at year end.
                                                    value={liveStats?.sin_last4 ? `•••• ${liveStats.sin_last4}` : liveStats?.sin_on_file ? "On file" : "Missing — cannot file T4A"}
                                                    mono
                                                />
                                                <DetailField icon={FileText} label="License Class" value={selected.license_class || "—"} />
                                                <DetailField icon={ShieldCheck} label="Regulatory Authority" value={selected.regulatory_authority || (selected.sgi_approved != null ? "SGI" : "—")} />
                                                <DetailField icon={MapPin} label="Regulatory Region" value={selected.regulatory_region || "—"} />
                                                <DetailField icon={ShieldCheck} label="Authority Approved" value={(selected.regulatory_authority_approved ?? selected.sgi_approved) === true ? "Yes" : (selected.regulatory_authority_approved ?? selected.sgi_approved) === false ? "No" : "Unknown"} />
                                                <DetailField icon={Clock} label="Authority Approved At" value={(selected.regulatory_authority_approved_at || selected.sgi_approved_at) ? new Date(selected.regulatory_authority_approved_at || selected.sgi_approved_at).toLocaleString("en-CA") : "—"} />
                                                <DetailField icon={ShieldCheck} label="Spinr Approved" value={selected.is_verified === true ? "Yes" : selected.is_verified === false ? "No" : "Unknown"} />
                                                <DetailField icon={Clock} label="Spinr Approved At" value={selected.verified_at ? new Date(selected.verified_at).toLocaleString("en-CA") : "—"} />
                                                <DetailField
                                                    icon={FileText}
                                                    label="Work Authorization"
                                                    value={workAuth(selected).expires_at
                                                        ? `${workAuth(selected).label} ${fmtDate(workAuth(selected).expires_at!)}`
                                                        : workAuth(selected).label}
                                                />
                                                <DetailField icon={Shield} label="Permanent Resident" value={WORK_AUTH_FLAG_LABELS[workAuth(selected).permanent_resident] || "Unknown"} />
                                                <DetailField icon={Shield} label="Citizen" value={WORK_AUTH_FLAG_LABELS[workAuth(selected).citizen] || "Unknown"} />
                                                <DetailField icon={FileText} label="Welcome Letter Ref #" value={selected.decal_number || "—"} mono />
                                                <DetailField icon={Clock} label="Welcome Letter Generated" value={selected.decal_generated_at ? new Date(selected.decal_generated_at).toLocaleString("en-CA") : "—"} />
                                                <DetailField icon={CheckCircle} label="Welcome Letter Sent" value={selected.decals_sent === true ? "Yes" : selected.decals_sent === false ? "No" : "Unknown"} />
                                                <DetailField icon={Clock} label="Welcome Letter Sent At" value={selected.decals_sent_at ? new Date(selected.decals_sent_at).toLocaleString("en-CA") : "—"} />
                                            </div>
                                        )}
                                    </DetailSection>
                                    <DetailSection title="Vehicle Change History" icon={FileText}>
                                        {vehicleHistory.length === 0 ? (
                                            <p className="text-xs text-muted-foreground">No vehicle changes recorded.</p>
                                        ) : (
                                            <div className="space-y-2">
                                                {vehicleHistory.slice(0, 20).map((h) => (
                                                    <div key={h.id} className="text-xs flex items-start gap-2">
                                                        <span className="text-muted-foreground whitespace-nowrap">{new Date(h.created_at).toLocaleDateString()}</span>
                                                        <span className="flex-1">
                                                            <span className="font-medium">{h.field.replace(/_/g, " ")}</span>:{" "}
                                                            <span className="line-through text-muted-foreground">{h.old_value || "—"}</span>{" → "}
                                                            <span className="font-semibold">{h.new_value || "—"}</span>
                                                            <span className="ml-1 text-[10px] text-muted-foreground">({h.changed_by_role})</span>
                                                        </span>
                                                    </div>
                                                ))}
                                            </div>
                                        )}
                                    </DetailSection>
                                    <DetailSection title="Spinr Pass" icon={CreditCard}>
                                        {(() => {
                                            const ss = selected.subscription_status;
                                            const plan = selected.subscription_plan;
                                            const exp = selected.subscription_expires_at;
                                            const expLabel = exp ? new Date(exp).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : null;
                                            if (ss === "active") {
                                                return (
                                                    <div className="flex items-center gap-3">
                                                        {/* eslint-disable-next-line no-restricted-syntax -- Spinr Pass brand violet, not a success/warning/destructive signal (#2816) */}
                                                        <div className="w-9 h-9 rounded-xl bg-violet-100 dark:bg-violet-900/30 flex items-center justify-center shrink-0">
                                                            {/* eslint-disable-next-line no-restricted-syntax -- Spinr Pass brand violet, not a success/warning/destructive signal (#2816) */}
                                                            <CreditCard className="h-4 w-4 text-violet-600 dark:text-violet-400" />
                                                        </div>
                                                        <div className="flex-1 min-w-0">
                                                            {/* eslint-disable-next-line no-restricted-syntax -- Spinr Pass brand violet, not a success/warning/destructive signal (#2816) */}
                                                            <p className="text-sm font-semibold text-violet-700 dark:text-violet-300">{plan || "Active Plan"}</p>
                                                            {/* eslint-disable-next-line no-restricted-syntax -- Spinr Pass brand violet, not a success/warning/destructive signal (#2816) */}
                                                            <p className="text-xs text-violet-600/70 dark:text-violet-400/70 mt-0.5">{expLabel ? `Renews / expires ${expLabel}` : "Subscription active"}</p>
                                                        </div>
                                                        {themeV2Enabled ? (
                                                            <Badge variant="outline-accent" className="text-[10px] font-bold uppercase tracking-wide shrink-0">Active</Badge>
                                                        ) : (
                                                            // eslint-disable-next-line no-restricted-syntax -- Spinr Pass brand violet, not a success/warning/destructive signal (#2816)
                                                            <span className="inline-flex items-center px-2.5 py-1 rounded-full bg-violet-100 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300 text-[10px] font-bold uppercase tracking-wide shrink-0">
                                                                Active
                                                            </span>
                                                        )}
                                                    </div>
                                                );
                                            }
                                            if (ss === "expired") {
                                                return (
                                                    <div className="flex items-center gap-3">
                                                        <div className="w-9 h-9 rounded-xl bg-destructive/15 flex items-center justify-center shrink-0">
                                                            <CreditCard className="h-4 w-4 text-destructive" />
                                                        </div>
                                                        <div className="flex-1 min-w-0">
                                                            <p className="text-sm font-semibold text-destructive">{plan || "Spinr Pass"}</p>
                                                            <p className="text-xs text-destructive/80 mt-0.5">{expLabel ? `Expired ${expLabel}` : "Subscription expired"}</p>
                                                        </div>
                                                        <span className="inline-flex items-center px-2.5 py-1 rounded-full bg-destructive/15 text-destructive dark:text-[#ff453a] text-[10px] font-bold uppercase tracking-wide shrink-0">
                                                            Expired
                                                        </span>
                                                    </div>
                                                );
                                            }
                                            return (
                                                <div className="flex items-center gap-3">
                                                    <div className="w-9 h-9 rounded-xl bg-muted flex items-center justify-center shrink-0">
                                                        <CreditCard className="h-4 w-4 text-muted-foreground" />
                                                    </div>
                                                    <p className="text-sm text-muted-foreground">No subscription</p>
                                                </div>
                                            );
                                        })()}
                                    </DetailSection>
                                    <div className="grid grid-cols-2 gap-2.5">
                                        <DetailField icon={CalendarRange} label="Joined" value={fmtDate(selected.created_at)} />
                                        <DetailField icon={Clock} label="Last Updated" value={fmtDate(selected.updated_at)} />
                                        {selected.account_deleted && (
                                            <DetailField icon={Trash2} label="Account Deleted" value={selected.deleted_at ? new Date(selected.deleted_at).toLocaleString("en-CA") : "Yes"} />
                                        )}
                                    </div>
        </>
    );
}
