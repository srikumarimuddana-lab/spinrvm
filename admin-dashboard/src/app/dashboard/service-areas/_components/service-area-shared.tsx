"use client";

import { useEffect, useState, lazy } from "react";
import { ToggleLeft, ToggleRight } from "lucide-react";

// ─── Shared cross-tab helpers (geo/city-preset utilities + lazy map) ───
// Extracted verbatim from service-areas/page.tsx — used by the top-level
// page (create form, airport sub-region form/list) and by GeneralTabForm.

export const GeofenceMap = lazy(() => import("@/components/geofence-map"));

export const regulatoryDefaultsForProvince = (province: string) => {
  if (province === "SK") return { authority: "SGI", region: "SK" };
  if (province === "AB") return { authority: "Alberta TNC / municipal licensing", region: "AB" };
  if (province === "ON") return { authority: "Municipal vehicle-for-hire / PTC licensing", region: "ON" };
  return { authority: "Provincial / municipal authority", region: province || "" };
};

export const CITY_PRESETS: Record<string, { city: string; province: string; center: { lat: number; lng: number }; polygon: { lat: number; lng: number }[] }> = {
  saskatoon: { city: "Saskatoon", province: "SK", center: { lat: 52.13, lng: -106.67 }, polygon: [{ lat: 52.19, lng: -106.75 }, { lat: 52.19, lng: -106.55 }, { lat: 52.08, lng: -106.55 }, { lat: 52.08, lng: -106.75 }] },
  regina: { city: "Regina", province: "SK", center: { lat: 50.45, lng: -104.62 }, polygon: [{ lat: 50.50, lng: -104.72 }, { lat: 50.50, lng: -104.52 }, { lat: 50.40, lng: -104.52 }, { lat: 50.40, lng: -104.72 }] },
  calgary: { city: "Calgary", province: "AB", center: { lat: 51.04, lng: -114.07 }, polygon: [{ lat: 51.15, lng: -114.25 }, { lat: 51.15, lng: -113.90 }, { lat: 50.90, lng: -113.90 }, { lat: 50.90, lng: -114.25 }] },
  edmonton: { city: "Edmonton", province: "AB", center: { lat: 53.55, lng: -113.49 }, polygon: [{ lat: 53.65, lng: -113.65 }, { lat: 53.65, lng: -113.35 }, { lat: 53.45, lng: -113.35 }, { lat: 53.45, lng: -113.65 }] },
  winnipeg: { city: "Winnipeg", province: "MB", center: { lat: 49.90, lng: -97.14 }, polygon: [{ lat: 49.97, lng: -97.30 }, { lat: 49.97, lng: -96.98 }, { lat: 49.80, lng: -96.98 }, { lat: 49.80, lng: -97.30 }] },
};

export function polygonToText(polygon: any[]) { return polygon.map(p => `${p.lat}, ${p.lng}`).join("\n"); }

/** Extract polygon points [{lat,lng}] from area data. Backend may store as `polygon` or `geojson`. */
export function getAreaPolygon(area: any): { lat: number; lng: number }[] {
  const geo = area.polygon || area.geojson;
  if (!geo) return [];
  // GeoJSON format: { type: "Polygon", coordinates: [[[lng,lat], ...]] }
  if (geo.type === "Polygon" && geo.coordinates?.[0]) {
    return geo.coordinates[0].map((c: number[]) => ({ lat: c[1], lng: c[0] }));
  }
  // Already an array of {lat,lng}
  if (Array.isArray(geo) && geo.length > 0 && geo[0].lat !== undefined) {
    return geo;
  }
  return [];
}

/** Get the center of an area's polygon for map centering. */
export function getAreaCenter(area: any): { lat: number; lng: number } {
  const pts = getAreaPolygon(area);
  if (pts.length === 0) {
    // Fallback to city preset
    const key = (area.city || "").toLowerCase();
    return CITY_PRESETS[key]?.center || { lat: 52.13, lng: -106.67 };
  }
  return {
    lat: pts.reduce((s, p) => s + p.lat, 0) / pts.length,
    lng: pts.reduce((s, p) => s + p.lng, 0) / pts.length,
  };
}

// ─── Inline Editable Field Components ───

export function FieldInput({ label, value, type = "text", onSave }: { label: string; value: any; type?: string; onSave: (v: string) => void }) {
  const [val, setVal] = useState(String(value));
  const [dirty, setDirty] = useState(false);
  useEffect(() => { setVal(String(value)); setDirty(false); }, [value]);
  return (
    <div>
      <label className="block text-xs font-semibold text-muted-foreground mb-1">{label}</label>
      <div className="flex gap-2">
        <input className="flex-1 border rounded-lg px-3 py-2 text-sm" type={type} step={type === 'number' ? '0.01' : undefined} value={val}
          onChange={e => { setVal(e.target.value); setDirty(true); }} />
        {dirty && <button onClick={() => { onSave(val); setDirty(false); }} className="px-3 py-1 bg-primary text-primary-foreground text-xs rounded-lg font-semibold">Save</button>}
      </div>
    </div>
  );
}

export function FieldTextarea({ label, value, placeholder, onSave }: { label: string; value: any; placeholder?: string; onSave: (v: string) => void }) {
  const [val, setVal] = useState(String(value ?? ''));
  const [dirty, setDirty] = useState(false);
  useEffect(() => { setVal(String(value ?? '')); setDirty(false); }, [value]);
  return (
    <div>
      <label className="block text-xs font-semibold text-muted-foreground mb-1">{label}</label>
      <textarea className="w-full border rounded-lg px-3 py-2 text-sm" rows={3} value={val} placeholder={placeholder}
        onChange={e => { setVal(e.target.value); setDirty(true); }} />
      {dirty && <button onClick={() => { onSave(val); setDirty(false); }} className="mt-1 px-3 py-1 bg-primary text-primary-foreground text-xs rounded-lg font-semibold">Save</button>}
    </div>
  );
}

export function _FieldSelect({ label, value, options, onSave }: { label: string; value: string; options: string[]; onSave: (v: string) => void }) {
  return (
    <div>
      <label className="block text-xs font-semibold text-muted-foreground mb-1">{label}</label>
      <select className="w-full border rounded-lg px-3 py-2 text-sm" value={value} onChange={e => onSave(e.target.value)}>
        {options.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    </div>
  );
}

export function FieldToggle({ label, value, onSave }: { label: string; value: boolean; onSave: (v: boolean) => void }) {
  return (
    <div className="flex items-center gap-2">
      <label className="text-xs font-semibold text-muted-foreground">{label}</label>
      <button onClick={() => onSave(!value)}>
        {value ? <ToggleRight className="h-6 w-6 text-success" /> : <ToggleLeft className="h-6 w-6 text-muted-foreground" />}
      </button>
    </div>
  );
}
