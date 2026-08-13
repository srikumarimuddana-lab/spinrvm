import { useState, useEffect, useMemo } from 'react';
import api from '@shared/api/client';

export type AirportZone = {
  id: string;
  name: string;
  is_airport: boolean;
  airport_fee: number;
  polygon: { lat: number; lng: number }[];
};

function pointInPolygon(
  lat: number,
  lng: number,
  poly: { lat: number; lng: number }[],
): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const yi = poly[i].lat, xi = poly[i].lng;
    const yj = poly[j].lat, xj = poly[j].lng;
    if (yi > lat !== yj > lat && lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

export function useAirportZones(
  serviceAreaId: string | null,
  isOnline: boolean,
  driverLat?: number | null,
  driverLng?: number | null,
) {
  const [zones, setZones] = useState<AirportZone[]>([]);

  useEffect(() => {
    if (!serviceAreaId || !isOnline) {
      setZones([]);
      return;
    }

    let cancelled = false;

    (async () => {
      try {
        const res = await api.get<AirportZone[]>(
          `/service-areas/${serviceAreaId}/airport-zones`
        );
        if (!cancelled) {
          setZones(res.data || []);
        }
      } catch {
        if (!cancelled) setZones([]);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [serviceAreaId, isOnline]);

  const activeZone = useMemo(() => {
    if (!zones.length || driverLat == null || driverLng == null) return null;
    return zones.find(z => z.polygon?.length >= 3 && pointInPolygon(driverLat, driverLng, z.polygon)) ?? null;
  }, [zones, driverLat, driverLng]);

  return { zones, activeZone };
}
