import { useState, useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { 
    adminCreateRide, 
    getUsersPaginated, 
    getDrivers, 
    adminPlacesAutocomplete, 
    adminPlacesDetails 
} from "@/lib/api";
import { Search, MapPin, User, Car } from "lucide-react";

function useDebounce<T>(value: T, delay: number): T {
    const [debouncedValue, setDebouncedValue] = useState<T>(value);
    useEffect(() => {
        const handler = setTimeout(() => setDebouncedValue(value), delay);
        return () => clearTimeout(handler);
    }, [value, delay]);
    return debouncedValue;
}

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

    // Rider State
    const [riderSearch, setRiderSearch] = useState("");
    const debouncedRiderSearch = useDebounce(riderSearch, 300);
    const [riderResults, setRiderResults] = useState<any[]>([]);
    const [selectedRider, setSelectedRider] = useState<{ id: string; name: string } | null>(null);
    const [riderFocused, setRiderFocused] = useState(false);

    // Driver State
    const [driverSearch, setDriverSearch] = useState("");
    const debouncedDriverSearch = useDebounce(driverSearch, 300);
    const [driverResults, setDriverResults] = useState<any[]>([]);
    const [selectedDriver, setSelectedDriver] = useState<{ id: string; name: string } | null>(null);
    const [driverFocused, setDriverFocused] = useState(false);

    // Address States
    const [pickupInput, setPickupInput] = useState("");
    const debouncedPickupInput = useDebounce(pickupInput, 400);
    const [pickupResults, setPickupResults] = useState<any[]>([]);
    const [selectedPickup, setSelectedPickup] = useState<{ lat: number; lng: number; address: string } | null>(null);
    const [pickupFocused, setPickupFocused] = useState(false);

    const [dropoffInput, setDropoffInput] = useState("");
    const debouncedDropoffInput = useDebounce(dropoffInput, 400);
    const [dropoffResults, setDropoffResults] = useState<any[]>([]);
    const [selectedDropoff, setSelectedDropoff] = useState<{ lat: number; lng: number; address: string } | null>(null);
    const [dropoffFocused, setDropoffFocused] = useState(false);

    const [fare, setFare] = useState("");

    // Fetch Riders
    useEffect(() => {
        if (!debouncedRiderSearch || selectedRider?.name === debouncedRiderSearch) {
            setRiderResults([]);
            return;
        }
        getUsersPaginated({ role: "rider", search: debouncedRiderSearch, limit: 5 })
            .then(res => setRiderResults(res))
            .catch(() => setRiderResults([]));
    }, [debouncedRiderSearch, selectedRider]);

    // Fetch Drivers
    useEffect(() => {
        if (!debouncedDriverSearch || selectedDriver?.name === debouncedDriverSearch) {
            setDriverResults([]);
            return;
        }
        getDrivers({ search: debouncedDriverSearch, limit: 5 })
            .then(res => setDriverResults(res))
            .catch(() => setDriverResults([]));
    }, [debouncedDriverSearch, selectedDriver]);

    // Fetch Pickup Places
    useEffect(() => {
        if (!debouncedPickupInput || selectedPickup?.address === debouncedPickupInput) {
            setPickupResults([]);
            return;
        }
        adminPlacesAutocomplete(debouncedPickupInput)
            .then(res => setPickupResults(res.predictions || []))
            .catch(() => setPickupResults([]));
    }, [debouncedPickupInput, selectedPickup]);

    // Fetch Dropoff Places
    useEffect(() => {
        if (!debouncedDropoffInput || selectedDropoff?.address === debouncedDropoffInput) {
            setDropoffResults([]);
            return;
        }
        adminPlacesAutocomplete(debouncedDropoffInput)
            .then(res => setDropoffResults(res.predictions || []))
            .catch(() => setDropoffResults([]));
    }, [debouncedDropoffInput, selectedDropoff]);


    const handlePlaceSelect = async (placeId: string, description: string, type: "pickup" | "dropoff") => {
        if (type === "pickup") {
            setPickupInput(description);
            setPickupFocused(false);
        } else {
            setDropoffInput(description);
            setDropoffFocused(false);
        }

        try {
            const details = await adminPlacesDetails(placeId);
            if (type === "pickup") {
                setSelectedPickup({
                    lat: details.lat,
                    lng: details.lng,
                    address: details.formatted_address || description
                });
                setPickupInput(details.formatted_address || description);
            } else {
                setSelectedDropoff({
                    lat: details.lat,
                    lng: details.lng,
                    address: details.formatted_address || description
                });
                setDropoffInput(details.formatted_address || description);
            }
        } catch (err) {
            setError(`Failed to fetch location details: ${err}`);
        }
    };


    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError("");

        if (!selectedRider) return setError("Please select a rider.");
        if (!selectedPickup) return setError("Please select a valid pickup location from the suggestions.");
        if (!selectedDropoff) return setError("Please select a valid dropoff location from the suggestions.");

        setLoading(true);
        try {
            await adminCreateRide({
                rider_id: selectedRider.id,
                driver_id: selectedDriver?.id || undefined,
                pickup_address: selectedPickup.address,
                pickup_lat: selectedPickup.lat,
                pickup_lng: selectedPickup.lng,
                dropoff_address: selectedDropoff.address,
                dropoff_lat: selectedDropoff.lat,
                dropoff_lng: selectedDropoff.lng,
                total_fare: fare ? fare : undefined,
            } as any);
            onSuccess();
            handleClose();
        } catch (err: any) {
            setError(err.message || "Failed to create ride");
        } finally {
            setLoading(false);
        }
    };

    const handleClose = () => {
        setRiderSearch("");
        setSelectedRider(null);
        setDriverSearch("");
        setSelectedDriver(null);
        setPickupInput("");
        setSelectedPickup(null);
        setDropoffInput("");
        setSelectedDropoff(null);
        setFare("");
        setError("");
        onClose();
    };

    if (!open) return null;

    return (
        <Dialog open={open} onOpenChange={(val) => !val && handleClose()}>
            <DialogContent className="max-w-md overflow-visible">
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
                                        onClick={() => {
                                            const name = `${u.first_name || ""} ${u.last_name || ""}`.trim() || u.email || u.phone;
                                            setSelectedRider({ id: u.id, name });
                                            setRiderSearch(name);
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
                        <Label>Driver (Optional)</Label>
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
                                        onClick={() => {
                                            setSelectedDriver({ id: d.id, name: d.name });
                                            setDriverSearch(d.name);
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
                        <Label>Pickup Location</Label>
                        <div className="relative mt-1">
                            <MapPin className="absolute left-2.5 top-2.5 h-4 w-4 text-blue-500" />
                            <Input
                                placeholder="Start typing address..."
                                value={pickupInput}
                                onChange={(e) => {
                                    setPickupInput(e.target.value);
                                    if (selectedPickup) setSelectedPickup(null);
                                }}
                                onFocus={() => setPickupFocused(true)}
                                onBlur={() => setTimeout(() => setPickupFocused(false), 200)}
                                className="pl-9 border-blue-200 focus-visible:ring-blue-500"
                                required
                            />
                        </div>
                        {pickupFocused && pickupResults.length > 0 && (
                            <div className="absolute z-50 mt-1 w-full rounded-md border bg-popover shadow-md">
                                {pickupResults.map((place) => (
                                    <div
                                        key={place.place_id}
                                        className="flex cursor-pointer flex-col px-3 py-2 hover:bg-muted"
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
                        <Label>Dropoff Location</Label>
                        <div className="relative mt-1">
                            <MapPin className="absolute left-2.5 top-2.5 h-4 w-4 text-red-500" />
                            <Input
                                placeholder="Start typing address..."
                                value={dropoffInput}
                                onChange={(e) => {
                                    setDropoffInput(e.target.value);
                                    if (selectedDropoff) setSelectedDropoff(null);
                                }}
                                onFocus={() => setDropoffFocused(true)}
                                onBlur={() => setTimeout(() => setDropoffFocused(false), 200)}
                                className="pl-9 border-red-200 focus-visible:ring-red-500"
                                required
                            />
                        </div>
                        {dropoffFocused && dropoffResults.length > 0 && (
                            <div className="absolute z-50 mt-1 w-full rounded-md border bg-popover shadow-md">
                                {dropoffResults.map((place) => (
                                    <div
                                        key={place.place_id}
                                        className="flex cursor-pointer flex-col px-3 py-2 hover:bg-muted"
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

                    {/* Fare */}
                    <div className="grid gap-2">
                        <Label>Total Fare (Optional)</Label>
                        <div className="relative">
                            <span className="absolute left-3 top-2.5 text-sm text-muted-foreground">$</span>
                            <Input 
                                type="number" 
                                step="0.01" 
                                placeholder="0.00"
                                value={fare} 
                                onChange={(e) => setFare(e.target.value)} 
                                className="pl-7"
                            />
                        </div>
                    </div>

                    {error && <p className="text-sm font-medium text-red-500">{error}</p>}
                    
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