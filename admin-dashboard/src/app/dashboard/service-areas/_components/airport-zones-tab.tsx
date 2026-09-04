"use client";

import { Suspense } from "react";
import { Plane } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  GeofenceMap, getAreaPolygon, getAreaCenter, FieldInput, FieldToggle,
} from "./service-area-shared";

// ─── Airport Zones (Sub-regions) Tab ───
// Extracted verbatim from service-areas/page.tsx (the "subregions" tab
// panel of the areas-list `.map()` loop). State (addAirportFor, airportForm,
// airportMapKey) and handlers stay owned by the parent ServiceAreasPage,
// exactly as before — passed down as props rather than duplicated here.

export default function AirportZonesTab({
  area,
  subRegions,
  themeV2Enabled,
  addAirportFor,
  setAddAirportFor,
  airportForm,
  setAirportForm,
  airportMapKey,
  setAirportMapKey,
  handleCreateAirportSubRegion,
  handleDelete,
  handleFieldUpdate,
}: {
  area: any;
  subRegions: any[];
  themeV2Enabled: boolean;
  addAirportFor: string | null;
  setAddAirportFor: (id: string | null) => void;
  airportForm: { name: string; airport_fee: number; polygon: any[] };
  setAirportForm: (form: { name: string; airport_fee: number; polygon: any[] }) => void;
  airportMapKey: number;
  setAirportMapKey: (updater: (k: number) => number) => void;
  handleCreateAirportSubRegion: (parentId: string) => Promise<void>;
  handleDelete: (id: string, name: string) => void;
  handleFieldUpdate: (areaId: string, field: string, value: any) => void;
}) {
  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h4 className="font-bold text-foreground">Airport Zones</h4>
          <p className="text-sm text-muted-foreground">Draw airport boundaries inside {area.name}. Rides to/from these zones get an extra airport surcharge.</p>
        </div>
        {addAirportFor !== area.id && (
          <button onClick={() => { setAddAirportFor(area.id); setAirportForm({ name: "", airport_fee: 2.0, polygon: [] }); setAirportMapKey(k => k + 1); }}
            // eslint-disable-next-line no-restricted-syntax -- decorative airport-feature brand accent, not a health-state signal (#2816)
            className="flex items-center gap-2 bg-blue-500 text-white px-4 py-2 rounded-xl text-sm font-semibold hover:bg-blue-600">
            <Plane className="h-4 w-4" /> Add Airport Zone
          </button>
        )}
      </div>

      {/* Add Airport Form */}
      {addAirportFor === area.id && (
        /* eslint-disable-next-line no-restricted-syntax -- decorative airport-feature brand accent, not a health-state signal (#2816) */
        <div className="bg-blue-50 border border-blue-200 dark:bg-blue-900/20 dark:border-blue-800 rounded-xl p-5 mb-5">
          {/* eslint-disable-next-line no-restricted-syntax -- decorative airport-feature brand accent, not a health-state signal (#2816) */}
          <h5 className="font-bold text-blue-900 dark:text-blue-200 mb-3 flex items-center gap-2">
            <Plane className="h-4 w-4" /> New Airport Zone in {area.name}
          </h5>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <div>
              {/* eslint-disable-next-line no-restricted-syntax -- decorative airport-feature brand accent, not a health-state signal (#2816) */}
              <label className="block text-xs font-semibold text-blue-800 dark:text-blue-300 mb-1">Airport Zone Name *</label>
              {/* eslint-disable-next-line no-restricted-syntax -- decorative airport-feature brand accent, not a health-state signal (#2816) */}
              <input className="w-full border border-blue-200 dark:border-blue-800 rounded-lg px-3 py-2 text-sm"
                value={airportForm.name}
                onChange={e => setAirportForm({ ...airportForm, name: e.target.value })}
                placeholder={`e.g. ${area.city || area.name} Airport`} />
            </div>
            <div>
              {/* eslint-disable-next-line no-restricted-syntax -- decorative airport-feature brand accent, not a health-state signal (#2816) */}
              <label className="block text-xs font-semibold text-blue-800 dark:text-blue-300 mb-1">Airport Fee ($)</label>
              {/* eslint-disable-next-line no-restricted-syntax -- decorative airport-feature brand accent, not a health-state signal (#2816) */}
              <input className="w-full border border-blue-200 dark:border-blue-800 rounded-lg px-3 py-2 text-sm"
                type="number" step="0.50" min="0"
                value={airportForm.airport_fee}
                onChange={e => setAirportForm({ ...airportForm, airport_fee: parseFloat(e.target.value) || 0 })} />
            </div>
          </div>
          <div className="mb-4">
            {/* eslint-disable-next-line no-restricted-syntax -- decorative airport-feature brand accent, not a health-state signal (#2816) */}
            <label className="block text-xs font-semibold text-blue-800 dark:text-blue-300 mb-2">
              Draw Airport Boundary on Map {airportForm.polygon.length === 0 && <span className="text-destructive">(required)</span>}
            </label>
            {/* eslint-disable-next-line no-restricted-syntax -- decorative airport-feature brand accent, not a health-state signal (#2816) */}
            <div className="h-64 rounded-xl overflow-hidden border border-blue-200 dark:border-blue-800">
              <Suspense fallback={<div className="h-full bg-muted flex items-center justify-center text-muted-foreground">Loading map...</div>}>
                <GeofenceMap
                  key={airportMapKey}
                  polygon={airportForm.polygon}
                  center={getAreaCenter(area)}
                  zoom={12}
                  onPolygonChange={(p: any) => setAirportForm({ ...airportForm, polygon: p })}
                />
              </Suspense>
            </div>
            {airportForm.polygon.length > 0 && <p className="text-xs text-success mt-1">{airportForm.polygon.length} points defined</p>}
          </div>
          <div className="flex gap-3">
            {/* eslint-disable-next-line no-restricted-syntax -- decorative airport-feature brand accent, not a health-state signal (#2816) */}
            <button onClick={() => handleCreateAirportSubRegion(area.id)} className="bg-blue-500 text-white px-5 py-2 rounded-xl text-sm font-semibold hover:bg-blue-600">Create Airport Zone</button>
            <button onClick={() => setAddAirportFor(null)} className="bg-muted text-foreground px-5 py-2 rounded-xl text-sm font-semibold">Cancel</button>
          </div>
        </div>
      )}

      {/* Existing Sub-regions */}
      {subRegions.length === 0 && addAirportFor !== area.id ? (
        <div className="text-center py-10 bg-muted rounded-xl">
          <Plane className="h-10 w-10 text-muted-foreground mx-auto mb-3" />
          <p className="text-muted-foreground font-medium">No airport zones yet</p>
          <p className="text-muted-foreground text-sm mt-1">Add an airport zone to automatically charge a surcharge for rides to/from the airport</p>
        </div>
      ) : (
        <div className="space-y-4">
          {subRegions.map((sub: any) => (
            // eslint-disable-next-line no-restricted-syntax -- decorative airport-feature brand accent, not a health-state signal (#2816)
            <div key={sub.id} className="bg-blue-50 border border-blue-100 dark:bg-blue-900/20 dark:border-blue-800 rounded-xl p-4">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  {/* eslint-disable-next-line no-restricted-syntax -- decorative airport-feature brand accent, not a health-state signal (#2816) */}
                  <Plane className="h-4 w-4 text-blue-600 dark:text-blue-400" />
                  {/* eslint-disable-next-line no-restricted-syntax -- decorative airport-feature brand accent, not a health-state signal (#2816) */}
                  <span className="font-bold text-blue-900 dark:text-blue-200">{sub.name}</span>
                  {themeV2Enabled ? (
                    <Badge variant="outline" className="text-xs">AIRPORT</Badge>
                  ) : (
                    // eslint-disable-next-line no-restricted-syntax -- decorative airport-category badge, not a health-state signal (#2816)
                    <span className="px-2 py-0.5 bg-blue-200 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300 text-xs font-bold rounded-md">AIRPORT</span>
                  )}
                  {!sub.is_active && <span className="px-2 py-0.5 bg-muted text-muted-foreground text-xs font-bold rounded-md">INACTIVE</span>}
                </div>
                <button onClick={() => handleDelete(sub.id, sub.name)} className="text-sm text-destructive hover:underline">Delete</button>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">
                <FieldInput label="Zone Name" value={sub.name} onSave={v => handleFieldUpdate(sub.id, 'name', v)} />
                <FieldInput label="Airport Fee ($)" value={sub.airport_fee || 0} type="number" onSave={v => handleFieldUpdate(sub.id, 'airport_fee', parseFloat(v))} />
                <FieldToggle label="Active" value={sub.is_active} onSave={v => handleFieldUpdate(sub.id, 'is_active', v)} />
              </div>
              {/* Airport zone boundary map */}
              <div>
                {/* eslint-disable-next-line no-restricted-syntax -- decorative airport-feature brand accent, not a health-state signal (#2816) */}
                <label className="block text-xs font-semibold text-blue-800 dark:text-blue-300 mb-2">Airport Zone Boundary</label>
                {/* eslint-disable-next-line no-restricted-syntax -- decorative airport-feature brand accent, not a health-state signal (#2816) */}
                <div className="h-56 rounded-xl overflow-hidden border border-blue-200 dark:border-blue-800">
                  <Suspense fallback={<div className="h-full bg-muted flex items-center justify-center text-muted-foreground">Loading map...</div>}>
                    <GeofenceMap
                      key={`sub-${sub.id}`}
                      polygon={getAreaPolygon(sub)}
                      center={getAreaPolygon(sub).length > 0 ? getAreaCenter(sub) : getAreaCenter(area)}
                      zoom={13}
                      onPolygonChange={(p: any) => {
                        const geojson = { type: "Polygon", coordinates: [p.map((pt: any) => [pt.lng, pt.lat])] };
                        handleFieldUpdate(sub.id, 'polygon', geojson);
                      }}
                    />
                  </Suspense>
                </div>
                {getAreaPolygon(sub).length > 0 && (
                  <p className="text-xs text-success mt-1">{getAreaPolygon(sub).length} boundary points</p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
