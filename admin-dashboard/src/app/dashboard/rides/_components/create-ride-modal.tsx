import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { adminCreateRide } from "@/lib/api";

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
  const [riderId, setRiderId] = useState("");
  const [driverId, setDriverId] = useState("");
  const [pickupAddress, setPickupAddress] = useState("");
  const [pickupLat, setPickupLat] = useState("");
  const [pickupLng, setPickupLng] = useState("");
  const [dropoffAddress, setDropoffAddress] = useState("");
  const [dropoffLat, setDropoffLat] = useState("");
  const [dropoffLng, setDropoffLng] = useState("");
  const [fare, setFare] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");

    try {
      await adminCreateRide({
        rider_id: riderId,
        driver_id: driverId,
        pickup_address: pickupAddress,
        pickup_lat: parseFloat(pickupLat),
        pickup_lng: parseFloat(pickupLng),
        dropoff_address: dropoffAddress,
        dropoff_lat: parseFloat(dropoffLat),
        dropoff_lng: parseFloat(dropoffLng),
        total_fare: parseFloat(fare),
      });
      onSuccess();
      onClose();
    } catch (err: any) {
      setError(err.message || "Failed to create ride");
    } finally {
      setLoading(false);
    }
  };

  if (!open) return null;

  return (
    <Dialog open={open} onOpenChange={(val) => !val && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Create Ride Manually</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid gap-2">
            <Label>Rider ID</Label>
            <Input required value={riderId} onChange={(e) => setRiderId(e.target.value)} />
          </div>
          <div className="grid gap-2">
            <Label>Driver ID (Optional)</Label>
            <Input value={driverId} onChange={(e) => setDriverId(e.target.value)} />
          </div>
          <div className="grid gap-2">
            <Label>Pickup Address</Label>
            <Input required value={pickupAddress} onChange={(e) => setPickupAddress(e.target.value)} />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <Label>Pickup Lat</Label>
              <Input type="number" step="any" required value={pickupLat} onChange={(e) => setPickupLat(e.target.value)} />
            </div>
            <div>
              <Label>Pickup Lng</Label>
              <Input type="number" step="any" required value={pickupLng} onChange={(e) => setPickupLng(e.target.value)} />
            </div>
          </div>
          <div className="grid gap-2">
            <Label>Dropoff Address</Label>
            <Input required value={dropoffAddress} onChange={(e) => setDropoffAddress(e.target.value)} />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <Label>Dropoff Lat</Label>
              <Input type="number" step="any" required value={dropoffLat} onChange={(e) => setDropoffLat(e.target.value)} />
            </div>
            <div>
              <Label>Dropoff Lng</Label>
              <Input type="number" step="any" required value={dropoffLng} onChange={(e) => setDropoffLng(e.target.value)} />
            </div>
          </div>
          <div className="grid gap-2">
            <Label>Total Fare (Optional)</Label>
            <Input type="number" step="any" value={fare} onChange={(e) => setFare(e.target.value)} />
          </div>
          {error && <p className="text-sm text-red-500">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={loading}>
              Cancel
            </Button>
            <Button type="submit" disabled={loading}>
              {loading ? "Creating..." : "Create Ride"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}