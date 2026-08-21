"use client";

import { useState, useEffect, useMemo } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import {
    adminCreateRide,
    adminFareEstimate,
    adminListVehicleTypes,
    adminPlacesDetails,
    adminPromoPreview,
    adminSearchDrivers,
    adminSearchUsers,
    type AdminFareEstimateResponse,
    type AdminVehicleType,
} from "@/lib/api";
import { useAdminLocation } from "@/hooks/useAdminLocation";
import { usePlacesAutocomplete } from "@/hooks/usePlacesAutocomplete";
import { Search, MapPin, User, Car } from "lucide-react";

function useDebounce<T>(value: T, delay: number): T {
    const [debouncedValue, setDebouncedValue] = useState<T>(value);
    useEffect(() => {
        const handler = setTimeout(() => setDebouncedValue(value), delay);
        return () => clearTimeout(handler);
    }, [value, delay]);
    return debouncedValue;
}

/** Great-circle distance in km (haversine). */
function distanceKm(aLat: number, aLng: number, bLat: number, bLng: number): number {
    const R = 6371;
    const toRad = (d: number) => (d * Math.PI) / 180;
    const dLat = toRad(bLat - aLat);
    const dLng = toRad(bLng - aLng);
    const s =
        Math.sin(dLat / 2) ** 2 +
        Math.cos(toRad(aLat)) * Math.cos(toRad(bLat)) * Math.sin(dLng / 2) ** 2;
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(s)));
}

interface SelectedPlace { lat: number; lng: number; address: string }
interface PriceBreakdown { subtotal: number; discount: number; final: number }

export function CreateRideModal({
    open,
    onClose,
    onSuccess,
}: {
    open: boolean;
    onClose: () => void;
    onSuccess: () => void;
}) {
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");

    // Rider
    const [riderSearch, setRiderSearch] = useState("");
    const debouncedRiderSearch = useDebounce(riderSearch, 300);
    const [riderResults, setRiderResults] = useState<any[]>([]);
    const [selectedRider, setSelectedRider] = useState<{ id: string; name: string } | null>(null);
    const [riderFocused, setRiderFocused] = useState(false);

    // Driver
    const [driverSearch, setDriverSearch] = useState("");
    const debouncedDriverSearch = useDebounce(driverSearch, 300);
    const [driverResults, setDriverResults] = useState<any[]>([]);
    const [selectedDriver, setSelectedDriver] = useState<{ id: string; name: string } | null>(null);
    const [driverFocused, setDriverFocused] = useState(false);

    // Addresses — biased to admin's location for pickup, to chosen pickup for dropoff
    const adminLoc = useAdminLocation();
    // Bias coordinates only — re-deriving on `adminLoc` identity changes would
    // create a new bias object every render and force a useEffect refetch.
    const pickupBias = useMemo(
        () => (adminLoc ? { lat: adminLoc.lat, lng: adminLoc.lng, radiusMeters: 20000 } : null),
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [adminLoc?.lat, adminLoc?.lng],
    );

    const [pickupInput, setPickupInput] = useState("");
    const [selectedPickup, setSelectedPickup] = useState<SelectedPlace | null>(null);
    const [pickupFocused, setPickupFocused] = useState(false);
    const pickupAutocomplete = usePlacesAutocomplete(
        selectedPickup ? "" : pickupInput,
        pickupBias,
    );

    const [dropoffInput, setDropoffInput] = useState("");
    const [selectedDropoff, setSelectedDropoff] = useState<SelectedPlace | null>(null);
    const [dropoffFocused, setDropoffFocused] = useState(false);
    // Dropoff biases to the pickup once chosen — searching "Walmart" for the
    // dropoff after selecting a downtown pickup returns nearby Walmarts, not
    // generic ones across Canada.
    const dropoffBias = useMemo(
        () =>
            selectedPickup
                ? { lat: selectedPickup.lat, lng: selectedPickup.lng, radiusMeters: 20000 }
                : pickupBias,
        [selectedPickup, pickupBias],
    );
    const dropoffAutocomplete = usePlacesAutocomplete(
        selectedDropoff ? "" : dropoffInput,
        dropoffBias,
    );

    // Vehicle type
    const [vehicleTypes, setVehicleTypes] = useState<AdminVehicleType[]>([]);
    const [vehicleTypeId, setVehicleTypeId] = useState<string>("");

    // Fare + promo
    const [estimate, setEstimate] = useState<AdminFareEstimateResponse | null>(null);
    const [estimating, setEstimating] = useState(false);
    const [promoCode, setPromoCode] = useState("");
    const [applyingPromo, setApplyingPromo] = useState(false);
    const [appliedPromo, setAppliedPromo] = useState<{ code: string; discount: number } | null>(null);
    const [finalFare, setFinalFare] = useState<string>("");
    const [fareEdited, setFareEdited] = useState(false);

    const breakdown = useMemo<PriceBreakdown | null>(() => {
        if (estimate == null) return null;
        const subtotal = estimate.grand_total;
        const discount = appliedPromo?.discount ?? 0;
        const final = Math.max(0, +(subtotal - discount).toFixed(2));
        return { subtotal, discount, final };
    }, [estimate, appliedPromo]);

    // Sync finalFare when breakdown changes — but never overwrite a manual edit.
    useEffect(() => {
        if (breakdown && !fareEdited) {
            setFinalFare(breakdown.final.toFixed(2));
        }
    }, [breakdown, fareEdited]);

    // Load vehicle types once per modal open. Intentionally NOT keyed on
    // vehicleTypeId — that's set inside the effect when there's only one option.
    useEffect(() => {
        if (!open) return;
        let alive = true;
        adminListVehicleTypes()
            .then((rows) => {
                if (!alive) return;
                const active = (rows || []).filter((v) => v.is_active);
                setVehicleTypes(active);
                if (!vehicleTypeId && active.length === 1) setVehicleTypeId(active[0].id);
            })
            .catch(() => {
                if (alive) setVehicleTypes([]);
            });
        return () => {
            alive = false;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open]);

    // Riders
    useEffect(() => {
        if (!debouncedRiderSearch || selectedRider?.name === debouncedRiderSearch) {
            setRiderResults([]);
            return;
        }
        const ctrl = new AbortController();
        adminSearchUsers({ role: "rider", search: debouncedRiderSearch, limit: 5 })
            .then((res) => { if (!ctrl.signal.aborted) setRiderResults(res); })
            .catch(() => { if (!ctrl.signal.aborted) setRiderResults([]); });
        return () => ctrl.abort();
    }, [debouncedRiderSearch, selectedRider]);

    // Drivers — only online + available
    useEffect(() => {
        if (!debouncedDriverSearch || selectedDriver?.name === debouncedDriverSearch) {
            setDriverResults([]);
            return;
        }
        const ctrl = new AbortController();
        adminSearchDrivers({ search: debouncedDriverSearch, limit: 5, is_online: true, is_available: true })
            .then((res) => { if (!ctrl.signal.aborted) setDriverResults(res); })
            .catch(() => { if (!ctrl.signal.aborted) setDriverResults([]); });
        return () => ctrl.abort();
    }, [debouncedDriverSearch, selectedDriver]);

    // Stale-estimate guard: any input that affects fare invalidates an existing estimate.
    useEffect(() => {
        setEstimate(null);
        setAppliedPromo(null);
        setFareEdited(false);
    }, [selectedPickup, selectedDropoff, vehicleTypeId]);

    const handlePlaceSelect = async (
        placeId: string,
        description: string,
        type: "pickup" | "dropoff",
    ) => {
        const ac = type === "pickup" ? pickupAutocomplete : dropoffAutocomplete;
        try {
            const details = await adminPlacesDetails(placeId, ac.sessionToken);
            ac.rotateSessionToken();
            const place: SelectedPlace = {
                lat: details.lat,
                lng: details.lng,
                address: details.formatted_address || description,
            };
            if (type === "pickup") {
                setSelectedPickup(place);
                setPickupInput(place.address);
                setPickupFocused(false);
            } else {
                setSelectedDropoff(place);
                setDropoffInput(place.address);
                setDropoffFocused(false);
            }
            ac.clear();
        } catch (err) {
            setError(`Failed to fetch location details: ${err}`);
        }
    };

    const handleEstimateFare = async () => {
        if (!selectedPickup || !selectedDropoff || !vehicleTypeId) return;
        setEstimating(true);
        setError("");
        try {
            const km = distanceKm(
                selectedPickup.lat,
                selectedPickup.lng,
                selectedDropoff.lat,
                selectedDropoff.lng,
            );
            // 30 km/h average urban speed + 5min buffer matches the dispatch
            // payload heuristic in backend/routes/admin/rides.py.
            const minutes = Math.max(1, Math.round((km / 30) * 60) + 5);
            const res = await adminFareEstimate({
                pickup_lat: selectedPickup.lat,
                pickup_lng: selectedPickup.lng,
                dropoff_lat: selectedDropoff.lat,
                dropoff_lng: selectedDropoff.lng,
                distance_km: +km.toFixed(2),
                duration_minutes: minutes,
                vehicle_type_id: vehicleTypeId,
            });
            setEstimate(res);
            setAppliedPromo(null);
            setFareEdited(false);
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : "Failed to estimate fare");
        } finally {
            setEstimating(false);
        }
    };

    const handleApplyPromo = async () => {
        if (!selectedRider || !estimate || !promoCode.trim()) return;
        setApplyingPromo(true);
        setError("");
        try {
            const res = await adminPromoPreview({
                rider_id: selectedRider.id,
                code: promoCode.trim(),
                // Strings preserve Decimal precision over the wire.
                ride_fare: estimate.grand_total.toFixed(2),
            });
            setAppliedPromo({ code: res.code, discount: Number(res.discount_amount) });
            setFareEdited(false);
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : "Promo code rejected");
        } finally {
            setApplyingPromo(false);
        }
    };

    const handleRemovePromo = () => {
        setAppliedPromo(null);
        setPromoCode("");
        setFareEdited(false);
    };

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError("");

        if (!selectedRider) return setError("Please select a rider.");
        if (!selectedPickup) return setError("Please select a valid pickup location from the suggestions.");
        if (!selectedDropoff) return setError("Please select a valid dropoff location from the suggestions.");

        const totalNum = parseFloat(finalFare || "0");
        if (Number.isNaN(totalNum) || totalNum < 0) {
            return setError("Total fare must be a non-negative number.");
        }

        setLoading(true);
        try {
            await adminCreateRide({
                rider_id: selectedRider.id,
                driver_id: selectedDriver?.id,
                pickup_address: selectedPickup.address,
                pickup_lat: selectedPickup.lat,
                pickup_lng: selectedPickup.lng,
                dropoff_address: selectedDropoff.address,
                dropoff_lat: selectedDropoff.lat,
                dropoff_lng: selectedDropoff.lng,
                vehicle_type_id: vehicleTypeId || undefined,
                subtotal_fare: estimate ? estimate.grand_total.toFixed(2) : undefined,
                discount_amount: appliedPromo ? appliedPromo.discount.toFixed(2) : undefined,
                promo_code: appliedPromo?.code,
                total_fare: finalFare ? totalNum.toFixed(2) : undefined,
                fare_overridden_by_admin: fareEdited,
            });
            onSuccess();
            handleClose();
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : "Failed to create ride");
        } finally {
            setLoading(false);
        }
    };

    const handleClose = () => {
        setRiderSearch(""); setSelectedRider(null);
        setDriverSearch(""); setSelectedDriver(null);
        setPickupInput(""); setSelectedPickup(null);
        setDropoffInput(""); setSelectedDropoff(null);
        setVehicleTypeId("");
        setEstimate(null); setAppliedPromo(null);
        setPromoCode(""); setFinalFare(""); setFareEdited(false);
        setError("");
        onClose();
    };

    if (!open) return null;

    const canEstimate = !!(selectedPickup && selectedDropoff && vehicleTypeId);

    return (
        <Dialog open={open} onOpenChange={(val) => !val && handleClose()}>
            <DialogContent className="max-w-md max-h-[90vh] overflow-y-auto overflow-x-visible">
                <DialogHeader>
                    <DialogTitle>Create Ride Manually</DialogTitle>
                </DialogHeader>
                <form onSubmit={handleSubmit} className="space-y-5">

                    {/* Rider Selection */}
                    <div className="relative">
                        <Label>Rider</Label>
                        <div className="relative mt-1">
                            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                            <Input
                                placeholder="Search by name, email, or phone..."
                                value={riderSearch}
                                onChange={(e) => {
                                    setRiderSearch(e.target.value);
                                    if (selectedRider) setSelectedRider(null);
                                }}
                                onFocus={() => setRiderFocused(true)}
                                onBlur={() => setTimeout(() => setRiderFocused(false), 200)}
                                className="pl-9"
                                required
                            />
                        </div>
                        {riderFocused && riderResults.length > 0 && (
                            <div className="absolute z-50 mt-1 w-full rounded-md border bg-popover shadow-md">
                                {riderResults.map((u) => (
                                    <div
                                        key={u.id}
                                        className="flex cursor-pointer items-center gap-3 px-3 py-2 hover:bg-muted"
                                        onMouseDown={(e) => e.preventDefault()}
                                        onClick={() => {
                                            const name = `${u.first_name || ""} ${u.last_name || ""}`.trim() || u.email || u.phone;
                                            setSelectedRider({ id: u.id, name });
                                            setRiderSearch(name);
                                            setRiderFocused(false);
                                        }}
                                    >
                                        <User className="h-4 w-4 text-muted-foreground" />
                                        <div className="flex flex-col">
                                            <span className="text-sm font-medium">{`${u.first_name || ""} ${u.last_name || ""}`.trim()}</span>
                                            <span className="text-xs text-muted-foreground">{u.phone || u.email}</span>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>

                    {/* Driver Selection */}
                    <div className="relative">
                        <Label>Driver (Optional — online &amp; available only)</Label>
                        <div className="relative mt-1">
                            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                            <Input
                                placeholder="Search driver by name or phone..."
                                value={driverSearch}
                                onChange={(e) => {
                                    setDriverSearch(e.target.value);
                                    if (selectedDriver) setSelectedDriver(null);
                                }}
                                onFocus={() => setDriverFocused(true)}
                                onBlur={() => setTimeout(() => setDriverFocused(false), 200)}
                                className="pl-9"
                            />
                        </div>
                        {driverFocused && driverResults.length > 0 && (
                            <div className="absolute z-50 mt-1 w-full rounded-md border bg-popover shadow-md">
                                {driverResults.map((d) => (
                                    <div
                                        key={d.id}
                                        className="flex cursor-pointer items-center gap-3 px-3 py-2 hover:bg-muted"
                                        onMouseDown={(e) => e.preventDefault()}
                                        onClick={() => {
                                            setSelectedDriver({ id: d.id, name: d.name });
                                            setDriverSearch(d.name);
                                            setDriverFocused(false);
                                        }}
                                    >
                                        <Car className="h-4 w-4 text-muted-foreground" />
                                        <div className="flex flex-col">
                                            <span className="text-sm font-medium">{d.name}</span>
                                            <span className="text-xs text-muted-foreground">{d.phone}</span>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>

                    {/* Pickup Address */}
                    <div className="relative">
                        <Label>
                            Pickup Location
                            {!adminLoc && (
                                <span className="ml-2 text-xs text-muted-foreground">
                                    (enable browser location for nearby matches)
                                </span>
                            )}
                        </Label>
                        <div className="relative mt-1">
                            <MapPin className="absolute left-2.5 top-2.5 h-4 w-4 text-blue-500" />
                            <Input
                                placeholder="Start typing address or place..."
                                value={pickupInput}
                                onChange={(e) => {
                                    setPickupInput(e.target.value);
                                    if (selectedPickup) setSelectedPickup(null);
                                }}
                                onFocus={() => setPickupFocused(true)}
                                onBlur={() => setTimeout(() => setPickupFocused(false), 200)}
                                className="pl-9 border-blue-200 dark:border-blue-800 focus-visible:ring-blue-500"
                                required
                            />
                        </div>
                        {pickupFocused && pickupAutocomplete.predictions.length > 0 && (
                            <div className="absolute z-50 mt-1 w-full rounded-md border bg-popover shadow-md">
                                {pickupAutocomplete.predictions.map((place: any) => (
                                    <div
                                        key={place.place_id}
                                        className="flex cursor-pointer flex-col px-3 py-2 hover:bg-muted"
                                        onMouseDown={(e) => e.preventDefault()}
                                        onClick={() => handlePlaceSelect(place.place_id, place.description, "pickup")}
                                    >
                                        <span className="text-sm font-medium text-foreground">{place.structured_formatting?.main_text || place.description}</span>
                                        {place.structured_formatting?.secondary_text && (
                                            <span className="text-xs text-muted-foreground">{place.structured_formatting.secondary_text}</span>
                                        )}
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>

                    {/* Dropoff Address */}
                    <div className="relative">
                        <Label>
                            Dropoff Location
                            {selectedPickup && (
                                <span className="ml-2 text-xs text-muted-foreground">(biased to pickup)</span>
                            )}
                        </Label>
                        <div className="relative mt-1">
                            <MapPin className="absolute left-2.5 top-2.5 h-4 w-4 text-red-500" />
                            <Input
                                placeholder="Start typing address or place..."
                                value={dropoffInput}
                                onChange={(e) => {
                                    setDropoffInput(e.target.value);
                                    if (selectedDropoff) setSelectedDropoff(null);
                                }}
                                onFocus={() => setDropoffFocused(true)}
                                onBlur={() => setTimeout(() => setDropoffFocused(false), 200)}
                                className="pl-9 border-red-200 dark:border-red-800 focus-visible:ring-red-500"
                                required
                            />
                        </div>
                        {dropoffFocused && dropoffAutocomplete.predictions.length > 0 && (
                            <div className="absolute z-50 mt-1 w-full rounded-md border bg-popover shadow-md">
                                {dropoffAutocomplete.predictions.map((place: any) => (
                                    <div
                                        key={place.place_id}
                                        className="flex cursor-pointer flex-col px-3 py-2 hover:bg-muted"
                                        onMouseDown={(e) => e.preventDefault()}
                                        onClick={() => handlePlaceSelect(place.place_id, place.description, "dropoff")}
                                    >
                                        <span className="text-sm font-medium text-foreground">{place.structured_formatting?.main_text || place.description}</span>
                                        {place.structured_formatting?.secondary_text && (
                                            <span className="text-xs text-muted-foreground">{place.structured_formatting.secondary_text}</span>
                                        )}
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>

                    {/* Vehicle Type */}
                    <div className="grid gap-2">
                        <Label>Vehicle Type</Label>
                        <Select value={vehicleTypeId} onValueChange={setVehicleTypeId}>
                            <SelectTrigger>
                                <SelectValue placeholder={vehicleTypes.length ? "Select vehicle type" : "Loading..."} />
                            </SelectTrigger>
                            <SelectContent>
                                {vehicleTypes.map((v) => (
                                    <SelectItem key={v.id} value={v.id}>
                                        {v.name}{v.capacity ? ` · seats ${v.capacity}` : ""}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>

                    {/* Estimate fare + breakdown */}
                    <div className="grid gap-2">
                        <Button
                            type="button"
                            variant="secondary"
                            onClick={handleEstimateFare}
                            disabled={!canEstimate || estimating}
                        >
                            {estimating ? "Estimating..." : estimate ? "Re-estimate Fare" : "Estimate Fare"}
                        </Button>

                        {estimate && breakdown && (
                            <div className="rounded-md border bg-muted/40 p-3 text-sm">
                                <div className="mb-2 text-xs uppercase tracking-wide text-muted-foreground">
                                    {estimate.service_area ? `Service area: ${estimate.service_area}` : "Fare breakdown"}
                                </div>
                                <FareRow label="Base" value={estimate.base_fare} />
                                <FareRow label="Distance" value={estimate.distance_fare} />
                                <FareRow label="Time" value={estimate.time_fare} />
                                <FareRow label="Booking fee" value={estimate.booking_fee} />
                                {estimate.surge_multiplier > 1 && (
                                    <FareRow label={`Surge ×${estimate.surge_multiplier.toFixed(2)}`} value={null} />
                                )}
                                {estimate.area_fees_total > 0 && (
                                    <FareRow label="Area fees" value={estimate.area_fees_total} />
                                )}
                                {estimate.tax_amount > 0 && (
                                    <FareRow label="Tax" value={estimate.tax_amount} />
                                )}
                                <div className="my-1 border-t" />
                                <FareRow label="Subtotal" value={breakdown.subtotal} bold />
                                {appliedPromo && (
                                    <FareRow
                                        label={`Promo ${appliedPromo.code}`}
                                        value={-appliedPromo.discount}
                                        accent
                                    />
                                )}
                                <FareRow label="Total" value={breakdown.final} bold />
                            </div>
                        )}
                    </div>

                    {/* Promo */}
                    {estimate && (
                        <div className="grid gap-2">
                            <Label>Promo Code (Optional)</Label>
                            <div className="flex gap-2">
                                <Input
                                    placeholder="e.g. WELCOME10"
                                    value={promoCode}
                                    onChange={(e) => setPromoCode(e.target.value.toUpperCase())}
                                    disabled={!!appliedPromo}
                                />
                                {appliedPromo ? (
                                    <Button type="button" variant="outline" onClick={handleRemovePromo}>
                                        Remove
                                    </Button>
                                ) : (
                                    <Button
                                        type="button"
                                        onClick={handleApplyPromo}
                                        disabled={!selectedRider || !promoCode.trim() || applyingPromo}
                                    >
                                        {applyingPromo ? "Applying..." : "Apply"}
                                    </Button>
                                )}
                            </div>
                        </div>
                    )}

                    {/* Final fare (editable override) */}
                    <div className="grid gap-2">
                        <Label>
                            Total Fare
                            {fareEdited && <span className="ml-2 text-xs text-warning">(overridden)</span>}
                        </Label>
                        <div className="relative">
                            <span className="absolute left-3 top-2.5 text-sm text-muted-foreground">$</span>
                            <Input
                                type="number"
                                step="0.01"
                                placeholder="0.00"
                                value={finalFare}
                                onChange={(e) => {
                                    setFinalFare(e.target.value);
                                    setFareEdited(true);
                                }}
                                className="pl-7"
                            />
                        </div>
                        <p className="text-xs text-muted-foreground">
                            Defaults to the estimated total minus any promo. Edit to override before submitting.
                        </p>
                    </div>

                    {error && <p className="text-sm font-medium text-destructive">{error}</p>}

                    <DialogFooter className="pt-2">
                        <Button type="button" variant="outline" onClick={handleClose} disabled={loading}>
                            Cancel
                        </Button>
                        <Button type="submit" disabled={loading || !selectedPickup || !selectedDropoff || !selectedRider}>
                            {loading ? "Creating..." : "Create Ride"}
                        </Button>
                    </DialogFooter>
                </form>
            </DialogContent>
        </Dialog>
    );
}

function FareRow({
    label,
    value,
    bold,
    accent,
}: {
    label: string;
    value: number | null;
    bold?: boolean;
    accent?: boolean;
}) {
    return (
        <div
            className={`flex items-center justify-between py-0.5 ${bold ? "font-semibold" : ""} ${accent ? "text-success" : ""}`}
        >
            <span>{label}</span>
            <span>{value == null ? "" : `$${value.toFixed(2)}`}</span>
        </div>
    );
}
