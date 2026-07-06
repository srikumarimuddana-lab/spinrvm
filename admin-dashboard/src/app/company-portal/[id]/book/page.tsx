"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { CalendarPlus, CheckCircle2, Loader2, MapPin } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
    companyRequest,
    companyBookingFareEstimate,
    createCompanyBooking,
    getPortalVehicleTypes,
    CompanyFareEstimate,
    CreateBookingResult,
    PortalVehicleType,
} from "@/lib/companyApi";

/**
 * Book a ride for a customer (Spinr for Business guest booking).
 *
 * Route metrics: pickup/dropoff come from the rider-token maps proxy
 * (places autocomplete + details); distance/duration use a haversine ×1.3
 * road-factor approximation. That approximation feeds BOTH the preview and
 * the booking, so what the booker sees is what the company is charged at
 * booking time — and completion re-prices on actual GPS distance anyway
 * (unless fare-lock is on), exactly like rider-app bookings.
 */

interface PlacePoint {
    address: string;
    lat: number;
    lng: number;
}

function haversineKm(a: PlacePoint, b: PlacePoint): number {
    const R = 6371;
    const dLat = ((b.lat - a.lat) * Math.PI) / 180;
    const dLng = ((b.lng - a.lng) * Math.PI) / 180;
    const s =
        Math.sin(dLat / 2) ** 2 +
        Math.cos((a.lat * Math.PI) / 180) * Math.cos((b.lat * Math.PI) / 180) * Math.sin(dLng / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(s));
}

interface Suggestion {
    place_id: string;
    description: string;
}

function AddressPicker({
    label,
    value,
    onSelect,
}: {
    label: string;
    value: PlacePoint | null;
    onSelect: (p: PlacePoint | null) => void;
}) {
    const [query, setQuery] = useState(value?.address ?? "");
    const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
    const [open, setOpen] = useState(false);
    const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);

    const search = useCallback((text: string) => {
        if (debounce.current) clearTimeout(debounce.current);
        if (text.trim().length < 3) {
            setSuggestions([]);
            return;
        }
        debounce.current = setTimeout(async () => {
            try {
                const res = await companyRequest<{ predictions?: Suggestion[] } | Suggestion[]>(
                    `/api/v1/maps/places/autocomplete?input=${encodeURIComponent(text)}`
                );
                const rows = Array.isArray(res) ? res : (res.predictions ?? []);
                setSuggestions(rows.slice(0, 6));
                setOpen(true);
            } catch {
                setSuggestions([]);
            }
        }, 300);
    }, []);

    const pick = async (s: Suggestion) => {
        setOpen(false);
        setQuery(s.description);
        try {
            const detail = await companyRequest<{
                result?: { geometry?: { location?: { lat: number; lng: number } } };
                lat?: number;
                lng?: number;
            }>(`/api/v1/maps/places/details?place_id=${encodeURIComponent(s.place_id)}`);
            const lat = detail.lat ?? detail.result?.geometry?.location?.lat;
            const lng = detail.lng ?? detail.result?.geometry?.location?.lng;
            if (typeof lat === "number" && typeof lng === "number") {
                onSelect({ address: s.description, lat, lng });
                return;
            }
        } catch {
            /* fall through to clearing */
        }
        onSelect(null);
    };

    return (
        <div className="relative">
            <label className="mb-1 block text-xs font-medium text-muted-foreground">{label}</label>
            <div className="relative">
                <MapPin className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                    className="pl-8"
                    placeholder="Search address…"
                    value={query}
                    onChange={(e) => {
                        setQuery(e.target.value);
                        onSelect(null);
                        search(e.target.value);
                    }}
                    onFocus={() => suggestions.length > 0 && setOpen(true)}
                    onBlur={() => setTimeout(() => setOpen(false), 200)}
                />
            </div>
            {open && suggestions.length > 0 && (
                <div className="absolute z-20 mt-1 w-full rounded-md border bg-background shadow-md">
                    {suggestions.map((s) => (
                        <button
                            key={s.place_id}
                            type="button"
                            className="block w-full px-3 py-2 text-left text-sm hover:bg-muted"
                            onMouseDown={(e) => e.preventDefault()}
                            onClick={() => pick(s)}
                        >
                            {s.description}
                        </button>
                    ))}
                </div>
            )}
            {value && <p className="mt-1 truncate text-xs text-emerald-700">✓ {value.address}</p>}
        </div>
    );
}

export default function CompanyBookRidePage() {
    const params = useParams();
    const companyId = typeof params?.id === "string" ? params.id : "";

    const [customerName, setCustomerName] = useState("");
    const [customerPhone, setCustomerPhone] = useState("");
    const [pickup, setPickup] = useState<PlacePoint | null>(null);
    const [dropoff, setDropoff] = useState<PlacePoint | null>(null);
    const [vehicleTypes, setVehicleTypes] = useState<PortalVehicleType[]>([]);
    const [vehicleTypeId, setVehicleTypeId] = useState("");
    const [when, setWhen] = useState<"now" | "scheduled">("now");
    const [scheduledTime, setScheduledTime] = useState("");
    const [notes, setNotes] = useState("");
    const [estimate, setEstimate] = useState<CompanyFareEstimate | null>(null);
    const [estimating, setEstimating] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [confirmPhone, setConfirmPhone] = useState(false);
    const [result, setResult] = useState<CreateBookingResult | null>(null);

    useEffect(() => {
        getPortalVehicleTypes()
            .then((rows) => {
                const active = rows.filter((v) => v.is_active !== false);
                setVehicleTypes(active);
                if (active.length > 0) setVehicleTypeId((prev) => prev || active[0].id);
            })
            .catch(() => setVehicleTypes([]));
    }, []);

    const route = useMemo(() => {
        if (!pickup || !dropoff) return null;
        const straight = haversineKm(pickup, dropoff);
        const distance_km = Math.max(0.5, Math.round(straight * 1.3 * 100) / 100);
        const duration_minutes = Math.max(3, Math.round((distance_km / 30) * 60)); // ~30 km/h city
        return { distance_km, duration_minutes };
    }, [pickup, dropoff]);

    useEffect(() => {
        if (!companyId || !pickup || !dropoff || !vehicleTypeId || !route) {
            setEstimate(null);
            return;
        }
        let cancelled = false;
        setEstimating(true);
        companyBookingFareEstimate(companyId, {
            pickup_lat: pickup.lat,
            pickup_lng: pickup.lng,
            dropoff_lat: dropoff.lat,
            dropoff_lng: dropoff.lng,
            distance_km: route.distance_km,
            duration_minutes: route.duration_minutes,
            vehicle_type_id: vehicleTypeId,
        })
            .then((fare) => !cancelled && setEstimate(fare))
            .catch(() => !cancelled && setEstimate(null))
            .finally(() => !cancelled && setEstimating(false));
        return () => {
            cancelled = true;
        };
    }, [companyId, pickup, dropoff, vehicleTypeId, route]);

    const phoneLooksValid = /^\+?1?\s*\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}$/.test(customerPhone.trim());
    const canSubmit =
        !!customerName.trim() &&
        phoneLooksValid &&
        !!pickup &&
        !!dropoff &&
        !!vehicleTypeId &&
        !!route &&
        (when === "now" || !!scheduledTime) &&
        confirmPhone &&
        !submitting;

    const handleSubmit = async () => {
        if (!pickup || !dropoff || !route) return;
        setSubmitting(true);
        setError(null);
        try {
            const digits = customerPhone.replace(/\D/g, "");
            const phone = digits.length === 10 ? `+1${digits}` : customerPhone.trim();
            const res = await createCompanyBooking(companyId, {
                customer_name: customerName.trim(),
                customer_phone: phone,
                pickup_address: pickup.address,
                pickup_lat: pickup.lat,
                pickup_lng: pickup.lng,
                dropoff_address: dropoff.address,
                dropoff_lat: dropoff.lat,
                dropoff_lng: dropoff.lng,
                distance_km: route.distance_km,
                duration_minutes: route.duration_minutes,
                vehicle_type_id: vehicleTypeId,
                scheduled_time: when === "scheduled" ? new Date(scheduledTime).toISOString() : null,
                rider_notes: notes.trim() || null,
            });
            setResult(res);
        } catch (e) {
            setError(e instanceof Error ? e.message : "Booking failed");
        } finally {
            setSubmitting(false);
        }
    };

    if (result) {
        return (
            <Card className="mx-auto max-w-xl">
                <CardHeader className="text-center">
                    <CheckCircle2 className="mx-auto h-10 w-10 text-emerald-600" />
                    <CardTitle>Ride booked</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3 text-sm">
                    <p>
                        {result.customer_has_app
                            ? "The customer has the Spinr app — the ride is already showing there, and they got a push notification."
                            : "The customer will get an SMS with the trip details, their pickup code, and a live tracking link."}
                    </p>
                    {result.tracking_url && (
                        <p className="break-all rounded bg-muted p-3">
                            Tracking link:{" "}
                            <a
                                href={result.tracking_url}
                                target="_blank"
                                rel="noreferrer"
                                className="text-primary underline"
                            >
                                {result.tracking_url}
                            </a>
                        </p>
                    )}
                    <p className="text-xs text-muted-foreground">
                        The pickup code is sent only to the customer — the driver needs it
                        from them to start the trip.
                    </p>
                    <div className="flex gap-2 pt-2">
                        <Button
                            className="flex-1"
                            variant="outline"
                            onClick={() => {
                                setResult(null);
                                setCustomerName("");
                                setCustomerPhone("");
                                setPickup(null);
                                setDropoff(null);
                                setNotes("");
                                setConfirmPhone(false);
                                setEstimate(null);
                            }}
                        >
                            Book another
                        </Button>
                        <Button className="flex-1" asChild>
                            <a href={`/company-portal/${companyId}/bookings`}>View bookings</a>
                        </Button>
                    </div>
                </CardContent>
            </Card>
        );
    }

    return (
        <div className="mx-auto max-w-2xl space-y-6">
            <header>
                <h1 className="flex items-center gap-2 text-xl font-semibold">
                    <CalendarPlus className="h-5 w-5" /> Book a ride for a customer
                </h1>
                <p className="text-sm text-muted-foreground">
                    The ride is billed to your company allowance. The customer needs no app —
                    they get everything by text.
                </p>
            </header>

            <Card>
                <CardContent className="space-y-4 p-5">
                    <div className="grid gap-4 md:grid-cols-2">
                        <div>
                            <label className="mb-1 block text-xs font-medium text-muted-foreground">
                                Customer name
                            </label>
                            <Input
                                placeholder="Pat Customer"
                                value={customerName}
                                onChange={(e) => setCustomerName(e.target.value)}
                            />
                        </div>
                        <div>
                            <label className="mb-1 block text-xs font-medium text-muted-foreground">
                                Customer phone
                            </label>
                            <Input
                                type="tel"
                                placeholder="(306) 555-0123"
                                value={customerPhone}
                                onChange={(e) => {
                                    setCustomerPhone(e.target.value);
                                    setConfirmPhone(false);
                                }}
                            />
                        </div>
                    </div>

                    <AddressPicker label="Pickup" value={pickup} onSelect={setPickup} />
                    <AddressPicker label="Dropoff" value={dropoff} onSelect={setDropoff} />

                    <div className="grid gap-4 md:grid-cols-2">
                        <div>
                            <label className="mb-1 block text-xs font-medium text-muted-foreground">
                                Vehicle type
                            </label>
                            <select
                                className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
                                value={vehicleTypeId}
                                onChange={(e) => setVehicleTypeId(e.target.value)}
                            >
                                {vehicleTypes.map((v) => (
                                    <option key={v.id} value={v.id}>
                                        {v.name}
                                        {v.capacity ? ` · ${v.capacity} seats` : ""}
                                    </option>
                                ))}
                            </select>
                        </div>
                        <div>
                            <label className="mb-1 block text-xs font-medium text-muted-foreground">
                                When
                            </label>
                            <div className="flex gap-2">
                                <select
                                    className="h-9 rounded-md border border-input bg-background px-3 text-sm"
                                    value={when}
                                    onChange={(e) => setWhen(e.target.value as "now" | "scheduled")}
                                >
                                    <option value="now">Now</option>
                                    <option value="scheduled">Schedule</option>
                                </select>
                                {when === "scheduled" && (
                                    <Input
                                        type="datetime-local"
                                        value={scheduledTime}
                                        min={(() => {
                                            // datetime-local is LOCAL wall-clock; toISOString() is
                                            // UTC. Offset to local so the min isn't shifted by the
                                            // browser's tz (e.g. +6h in Saskatchewan), which would
                                            // block scheduling for the next several hours.
                                            const d = new Date(Date.now() + 15 * 60_000);
                                            d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
                                            return d.toISOString().slice(0, 16);
                                        })()}
                                        onChange={(e) => setScheduledTime(e.target.value)}
                                    />
                                )}
                            </div>
                        </div>
                    </div>

                    <div>
                        <label className="mb-1 block text-xs font-medium text-muted-foreground">
                            Note for the driver (optional)
                        </label>
                        <Input
                            placeholder="e.g. Customer waiting at the service desk"
                            maxLength={200}
                            value={notes}
                            onChange={(e) => setNotes(e.target.value)}
                        />
                    </div>

                    {estimate && (
                        <div className="rounded-md border bg-muted/40 p-3 text-sm">
                            <div className="flex items-center justify-between font-medium">
                                <span>Estimated fare (no surge on company rides)</span>
                                <span>${Number(estimate.grand_total).toFixed(2)}</span>
                            </div>
                            <div className="mt-1 text-xs text-muted-foreground">
                                Fare ${Number(estimate.subtotal).toFixed(2)} · Fees $
                                {Number(estimate.area_fees_total).toFixed(2)} · Tax $
                                {Number(estimate.tax_amount).toFixed(2)}
                                {estimate.service_area ? ` · ${estimate.service_area}` : ""}
                            </div>
                        </div>
                    )}
                    {estimating && (
                        <p className="text-xs text-muted-foreground">
                            <Loader2 className="mr-1 inline h-3 w-3 animate-spin" /> Estimating fare…
                        </p>
                    )}

                    <label className="flex items-start gap-2 text-xs text-muted-foreground">
                        <input
                            type="checkbox"
                            className="mt-0.5"
                            checked={confirmPhone}
                            onChange={(e) => setConfirmPhone(e.target.checked)}
                        />
                        <span>
                            I&apos;ve double-checked the customer&apos;s phone number
                            {customerPhone.trim() ? ` (${customerPhone.trim()})` : ""} — the pickup
                            code and tracking link are texted to it.
                        </span>
                    </label>

                    {error && <p className="rounded bg-red-50 p-3 text-sm text-red-700">{error}</p>}

                    <Button className="w-full" disabled={!canSubmit} onClick={handleSubmit}>
                        {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                        {when === "now" ? "Book ride now" : "Schedule ride"}
                    </Button>
                </CardContent>
            </Card>
        </div>
    );
}
