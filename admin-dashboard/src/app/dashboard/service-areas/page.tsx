"use client";

import { useEffect, useState, lazy, Suspense } from "react";
import { getServiceAreas, createServiceArea, updateServiceArea, deleteServiceArea, getSubscriptionPlans, createSubscriptionPlan, updateSubscriptionPlan, deleteSubscriptionPlan, getDriverSubscriptions, getAreaFees, createAreaFee, updateAreaFee, deleteAreaFee } from "@/lib/api";
import { Infinity as InfinityIcon } from "lucide-react";
import { Plus, Trash2, Pencil, MapPin, Settings, DollarSign, Car, CreditCard, ChevronDown, ChevronUp, ToggleLeft, ToggleRight, X, FileText, GripVertical, Clock, ShieldCheck, ShieldAlert, CheckCircle, AlertTriangle, Image, Plane } from "lucide-react";

const GeofenceMap = lazy(() => import("@/components/geofence-map"));

const CITY_PRESETS: Record<string, { city: string; province: string; center: { lat: number; lng: number }; polygon: { lat: number; lng: number }[] }> = {
  saskatoon: { city: "Saskatoon", province: "SK", center: { lat: 52.13, lng: -106.67 }, polygon: [{ lat: 52.19, lng: -106.75 }, { lat: 52.19, lng: -106.55 }, { lat: 52.08, lng: -106.55 }, { lat: 52.08, lng: -106.75 }] },
  regina: { city: "Regina", province: "SK", center: { lat: 50.45, lng: -104.62 }, polygon: [{ lat: 50.50, lng: -104.72 }, { lat: 50.50, lng: -104.52 }, { lat: 50.40, lng: -104.52 }, { lat: 50.40, lng: -104.72 }] },
  calgary: { city: "Calgary", province: "AB", center: { lat: 51.04, lng: -114.07 }, polygon: [{ lat: 51.15, lng: -114.25 }, { lat: 51.15, lng: -113.90 }, { lat: 50.90, lng: -113.90 }, { lat: 50.90, lng: -114.25 }] },
  edmonton: { city: "Edmonton", province: "AB", center: { lat: 53.55, lng: -113.49 }, polygon: [{ lat: 53.65, lng: -113.65 }, { lat: 53.65, lng: -113.35 }, { lat: 53.45, lng: -113.35 }, { lat: 53.45, lng: -113.65 }] },
  winnipeg: { city: "Winnipeg", province: "MB", center: { lat: 49.90, lng: -97.14 }, polygon: [{ lat: 49.97, lng: -97.30 }, { lat: 49.97, lng: -96.98 }, { lat: 49.80, lng: -96.98 }, { lat: 49.80, lng: -97.30 }] },
};

function polygonToText(polygon: any[]) { return polygon.map(p => `${p.lat}, ${p.lng}`).join("\n"); }

/** Extract polygon points [{lat,lng}] from area data. Backend may store as `polygon` or `geojson`. */
function getAreaPolygon(area: any): { lat: number; lng: number }[] {
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
function getAreaCenter(area: any): { lat: number; lng: number } {
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

export default function ServiceAreasPage() {
  const [areas, setAreas] = useState<any[]>([]);
  const [plans, setPlans] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [editTab, setEditTab] = useState("general");
  const [showCreate, setShowCreate] = useState(false);
  const [areaFees, setAreaFees] = useState<Record<string, any[]>>({});
  const [feesLoading, setFeesLoading] = useState<string | null>(null);

  const loadAreaFees = async (areaId: string) => {
    setFeesLoading(areaId);
    try {
      const fees = await getAreaFees(areaId);
      setAreaFees(prev => ({ ...prev, [areaId]: fees }));
    } catch {}
    setFeesLoading(null);
  };

  // Create form
  const [createForm, setCreateForm] = useState({
    name: "", city: "", province: "SK", preset: "",
    polygon: [] as any[], polygonText: "",
    is_active: true, is_airport: false,
  });
  const [mapKey, setMapKey] = useState(0);

  // Airport sub-region create form
  const [addAirportFor, setAddAirportFor] = useState<string | null>(null);
  const [airportForm, setAirportForm] = useState({ name: "", airport_fee: 2.0, polygon: [] as any[] });
  const [airportMapKey, setAirportMapKey] = useState(0);

  useEffect(() => { load(); }, []);

  const load = async () => {
    setLoading(true);
    try {
      const [a, p] = await Promise.all([getServiceAreas(), getSubscriptionPlans().catch(() => [])]);
      setAreas(a); setPlans(p);
    } catch {}
    setLoading(false);
  };

  const handlePreset = (key: string) => {
    if (key === "custom") {
      setCreateForm({ ...createForm, preset: key, city: "", polygon: [], polygonText: "" });
      setMapKey(k => k + 1);
      return;
    }
    const p = CITY_PRESETS[key];
    if (p) {
      setCreateForm({
        ...createForm, preset: key, name: createForm.name || p.city,
        city: p.city, province: p.province,
        polygon: p.polygon, polygonText: polygonToText(p.polygon),
      });
      setMapKey(k => k + 1);
    }
  };

  const handleCreate = async () => {
    if (!createForm.name) return;
    try {
      await createServiceArea({
        name: createForm.name, city: createForm.city, province: createForm.province,
        geojson: { type: "Polygon", coordinates: [createForm.polygon.map(p => [p.lng, p.lat])] },
        is_active: createForm.is_active, is_airport: createForm.is_airport,
        // Defaults
        platform_fee: 0, city_fee: 0.50, airport_fee: createForm.is_airport ? 5.0 : 0,
        gst_rate: 5.0, pst_rate: createForm.province === 'SK' ? 6.0 : createForm.province === 'AB' ? 0 : 7.0,
        insurance_fee_percent: 2.0, vehicle_pricing: [], subscription_plan_ids: [],
        spinr_pass_enabled: true, surge_enabled: false, surge_multiplier: 1.0,
        max_pickup_radius_km: 5.0, currency: 'CAD',
      });
      setShowCreate(false);
      setCreateForm({ name: "", city: "", province: "SK", preset: "", polygon: [], polygonText: "", is_active: true, is_airport: false });
      load();
    } catch (e: any) { alert(e?.message || "Failed to create"); }
  };

  const handleCreateAirportSubRegion = async (parentId: string) => {
    const parent = areas.find(a => a.id === parentId);
    if (!airportForm.name || airportForm.polygon.length < 3) {
      alert("Please enter a name and draw the airport boundary on the map.");
      return;
    }
    try {
      await createServiceArea({
        name: airportForm.name,
        city: parent?.city || "",
        province: parent?.province || "SK",
        geojson: { type: "Polygon", coordinates: [airportForm.polygon.map((p: any) => [p.lng, p.lat])] },
        is_active: true,
        is_airport: true,
        parent_service_area_id: parentId,
        airport_fee: airportForm.airport_fee,
      });
      setAddAirportFor(null);
      setAirportForm({ name: "", airport_fee: 2.0, polygon: [] });
      load();
    } catch (e: any) { alert(e?.message || "Failed to create airport zone"); }
  };

  const handleFieldUpdate = async (areaId: string, field: string, value: any) => {
    try {
      await updateServiceArea(areaId, { [field]: value });
      setAreas(prev => prev.map(a => {
        if (a.id === areaId) return { ...a, [field]: value };
        if (a.sub_regions?.length) {
          return { ...a, sub_regions: a.sub_regions.map((s: any) => s.id === areaId ? { ...s, [field]: value } : s) };
        }
        return a;
      }));
    } catch (e: any) { alert("Failed to update: " + (e?.message || "")); }
  };

  const handleVehiclePricingUpdate = async (areaId: string, pricing: any[]) => {
    await handleFieldUpdate(areaId, 'vehicle_pricing', pricing);
  };

  const handleDelete = async (id: string, name: string) => {
    if (!confirm(`Delete "${name}"? This cannot be undone.`)) return;
    await deleteServiceArea(id);
    load();
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Service Areas</h1>
          <p className="text-gray-500 mt-1">Configure pricing, fees, taxes & subscriptions per area</p>
        </div>
        <button onClick={() => setShowCreate(true)} className="flex items-center gap-2 bg-red-500 text-white px-5 py-2.5 rounded-xl font-semibold hover:bg-red-600">
          <Plus className="h-5 w-5" /> New Area
        </button>
      </div>

      {/* Create Form */}
      {showCreate && (
        <div className="bg-white rounded-2xl border p-6 mb-6 shadow-sm">
          <h3 className="text-lg font-bold mb-4">Create Service Area</h3>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">City Preset</label>
              <select className="w-full border rounded-xl px-4 py-2.5 text-sm" value={createForm.preset} onChange={e => handlePreset(e.target.value)}>
                <option value="">Select city...</option>
                {Object.entries(CITY_PRESETS).map(([k, v]) => <option key={k} value={k}>{v.city}, {v.province}</option>)}
                <option value="custom">Custom</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Area Name *</label>
              <input className="w-full border rounded-xl px-4 py-2.5 text-sm" value={createForm.name} onChange={e => setCreateForm({...createForm, name: e.target.value})} placeholder="e.g. Saskatoon Metro" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Province</label>
              <select className="w-full border rounded-xl px-4 py-2.5 text-sm" value={createForm.province} onChange={e => setCreateForm({...createForm, province: e.target.value})}>
                {['SK','AB','MB','ON','BC','QC','NS','NB','PE','NL','NT','YT','NU'].map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
          </div>
          <div className="flex items-center gap-4 mb-4">
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={createForm.is_active} onChange={e => setCreateForm({...createForm, is_active: e.target.checked})} className="accent-red-500" /> Active</label>
          </div>

          {/* Geofence Map — always visible, draw polygon or select preset */}
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-600 mb-2">
              Service Area Boundary {createForm.polygon.length === 0 && <span className="text-red-500">(select a preset or draw on the map)</span>}
            </label>
            <div className="h-64 rounded-xl overflow-hidden border">
              <Suspense fallback={<div className="h-full bg-gray-100 flex items-center justify-center text-gray-400">Loading map...</div>}>
                <GeofenceMap
                  key={mapKey}
                  polygon={createForm.polygon}
                  center={CITY_PRESETS[createForm.preset]?.center || { lat: 52.13, lng: -106.67 }}
                  zoom={createForm.polygon.length > 0 ? 11 : 5}
                  onPolygonChange={(p: any) => setCreateForm({...createForm, polygon: p, polygonText: polygonToText(p)})}
                />
              </Suspense>
            </div>
            {createForm.polygon.length > 0 && (
              <p className="text-xs text-green-600 mt-1">{createForm.polygon.length} points defined</p>
            )}
          </div>

          <div className="flex gap-3">
            <button onClick={handleCreate} className="bg-red-500 text-white px-6 py-2.5 rounded-xl font-semibold hover:bg-red-600">Create</button>
            <button onClick={() => setShowCreate(false)} className="bg-gray-100 text-gray-600 px-6 py-2.5 rounded-xl font-semibold">Cancel</button>
          </div>
        </div>
      )}

      {/* Areas List */}
      {loading ? (
        <div className="text-center py-12 text-gray-400">Loading...</div>
      ) : areas.length === 0 ? (
        <div className="text-center py-16 bg-white rounded-2xl border">
          <MapPin className="h-12 w-12 text-gray-300 mx-auto mb-4" />
          <h3 className="text-lg font-bold text-gray-700">No service areas</h3>
          <p className="text-gray-400">Create your first service area to start operations</p>
        </div>
      ) : (
        <div className="space-y-3">
          {areas.map(area => {
            const isExpanded = expandedId === area.id;
            const subRegions: any[] = area.sub_regions || [];
            return (
              <div key={area.id} className="bg-white rounded-2xl border overflow-hidden">
                {/* Area Header — click to expand */}
                <div className="flex items-center gap-4 p-5 cursor-pointer" onClick={() => { const newId = isExpanded ? null : area.id; setExpandedId(newId); setEditTab("general"); if (newId && !areaFees[newId]) loadAreaFees(newId); }}>
                  <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${area.is_active ? 'bg-green-100' : 'bg-gray-100'}`}>
                    <MapPin className={`h-5 w-5 ${area.is_active ? 'text-green-600' : 'text-gray-400'}`} />
                  </div>
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <h4 className="font-bold text-gray-900">{area.name}</h4>
                      {area.is_airport && <span className="px-2 py-0.5 bg-blue-100 text-blue-700 text-xs font-bold rounded-md">AIRPORT</span>}
                      {!area.is_active && <span className="px-2 py-0.5 bg-gray-100 text-gray-500 text-xs font-bold rounded-md">INACTIVE</span>}
                      {subRegions.length > 0 && <span className="px-2 py-0.5 bg-violet-100 text-violet-700 text-xs font-bold rounded-md">{subRegions.length} airport zone{subRegions.length > 1 ? 's' : ''}</span>}
                    </div>
                    <p className="text-sm text-gray-500">{area.city || ''}{area.province ? `, ${area.province}` : ''} · GST {area.gst_rate || 5}% · PST {area.pst_rate || 0}%</p>
                  </div>
                  <div className="text-sm text-gray-400">{area.vehicle_pricing?.length || 0} vehicles · {area.subscription_plan_ids?.length || 0} plans</div>
                  {isExpanded ? <ChevronUp className="h-5 w-5 text-gray-400" /> : <ChevronDown className="h-5 w-5 text-gray-400" />}
                </div>

                {/* Expanded Config */}
                {isExpanded && (
                  <div className="border-t">
                    {/* Tabs */}
                    <div className="flex gap-1 px-5 pt-3 bg-gray-50 overflow-x-auto">
                      {[
                        { key: 'general', label: 'General', icon: Settings },
                        { key: 'pricing', label: 'Vehicle Pricing', icon: Car },
                        { key: 'fees', label: 'Fees & Taxes', icon: DollarSign },
                        { key: 'subscriptions', label: 'Spinr Pass', icon: CreditCard },
                        { key: 'documents', label: 'Documents', icon: FileText },
                        { key: 'subregions', label: 'Airport Zones', icon: Plane },
                      ].map(tab => (
                        <button key={tab.key} onClick={() => setEditTab(tab.key)}
                          className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-semibold rounded-t-lg transition ${editTab === tab.key ? 'bg-white text-red-500 border-t-2 border-red-500' : 'text-gray-500 hover:text-gray-700'}`}>
                          <tab.icon className="h-4 w-4" /> {tab.label}
                        </button>
                      ))}
                    </div>

                    <div className="p-5">
                      {/* General Tab */}
                      {editTab === 'general' && (
                        <div className="space-y-6">
                          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                            <FieldInput label="Area Name" value={area.name} onSave={v => handleFieldUpdate(area.id, 'name', v)} />
                            <FieldInput label="City" value={area.city || ''} onSave={v => handleFieldUpdate(area.id, 'city', v)} />
                            <FieldSelect label="Province" value={area.province || 'SK'} options={['SK','AB','MB','ON','BC','QC','NS','NB','PE','NL']} onSave={v => handleFieldUpdate(area.id, 'province', v)} />
                            <FieldInput label="Pickup Radius (km)" value={area.max_pickup_radius_km || 5} type="number" onSave={v => handleFieldUpdate(area.id, 'max_pickup_radius_km', parseFloat(v))} />
                            <FieldToggle label="Active" value={area.is_active} onSave={v => handleFieldUpdate(area.id, 'is_active', v)} />
                          </div>

                          {/* Driver Matching */}
                          <div>
                            <h4 className="font-bold text-gray-800 mb-2">Driver Matching</h4>
                            <p className="text-sm text-gray-500 mb-3">Configure how drivers are matched to rides in this area.</p>
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                              <FieldSelect label="Matching Algorithm" value={area.driver_matching_algorithm || 'nearest'} options={['nearest', 'rating_based', 'round_robin', 'combined']} onSave={v => handleFieldUpdate(area.id, 'driver_matching_algorithm', v)} />
                              <FieldInput label="Search Radius (km)" value={area.search_radius_km || 10} type="number" onSave={v => handleFieldUpdate(area.id, 'search_radius_km', parseFloat(v))} />
                              <FieldInput label="Min Driver Rating" value={area.min_driver_rating || 4.0} type="number" onSave={v => handleFieldUpdate(area.id, 'min_driver_rating', parseFloat(v))} />
                            </div>
                          </div>

                          {/* Geofence Editor */}
                          <div>
                            <h4 className="font-bold text-gray-800 mb-2">Service Area Boundary</h4>
                            <p className="text-sm text-gray-500 mb-3">Draw or edit the polygon to define the service area boundary. Drivers and riders must be within this zone.</p>
                            <div className="h-80 rounded-xl overflow-hidden border">
                              <Suspense fallback={<div className="h-full bg-gray-100 flex items-center justify-center text-gray-400">Loading map...</div>}>
                                <GeofenceMap
                                  key={`edit-${area.id}`}
                                  polygon={getAreaPolygon(area)}
                                  center={getAreaCenter(area)}
                                  zoom={11}
                                  onPolygonChange={(p: any) => {
                                    const geojson = { type: "Polygon", coordinates: [p.map((pt: any) => [pt.lng, pt.lat])] };
                                    handleFieldUpdate(area.id, 'polygon', geojson);
                                  }}
                                />
                              </Suspense>
                            </div>
                            {getAreaPolygon(area).length > 0 && (
                              <p className="text-xs text-green-600 mt-1">{getAreaPolygon(area).length} boundary points defined</p>
                            )}
                          </div>

                          <div className="flex justify-end">
                            <button onClick={() => handleDelete(area.id, area.name)} className="text-sm text-red-500 hover:underline">Delete this area</button>
                          </div>
                        </div>
                      )}

                      {/* Vehicle Pricing Tab */}
                      {editTab === 'pricing' && (
                        <VehiclePricingEditor pricing={area.vehicle_pricing || []} onSave={p => handleVehiclePricingUpdate(area.id, p)} />
                      )}

                      {/* Fees & Taxes Tab */}
                      {editTab === 'fees' && (
                        <AreaFeesEditor
                          areaId={area.id}
                          area={area}
                          fees={areaFees[area.id] || []}
                          loading={feesLoading === area.id}
                          onReload={() => loadAreaFees(area.id)}
                          onFieldUpdate={handleFieldUpdate}
                        />
                      )}

                      {/* Spinr Pass Tab */}
                      {editTab === 'subscriptions' && (
                        <SpinrPassAreaTab
                          area={area}
                          plans={plans}
                          onToggle={v => handleFieldUpdate(area.id, 'spinr_pass_enabled', v)}
                          onPlansChanged={load}
                        />
                      )}

                      {/* Documents Tab */}
                      {editTab === 'documents' && (
                        <DocumentsEditor
                          docs={area.required_documents || []}
                          onSave={d => handleFieldUpdate(area.id, 'required_documents', d)}
                        />
                      )}

                      {/* Airport Zones (Sub-regions) Tab */}
                      {editTab === 'subregions' && (
                        <div>
                          <div className="flex items-center justify-between mb-4">
                            <div>
                              <h4 className="font-bold text-gray-800">Airport Zones</h4>
                              <p className="text-sm text-gray-500">Draw airport boundaries inside {area.name}. Rides to/from these zones get an extra airport surcharge.</p>
                            </div>
                            {addAirportFor !== area.id && (
                              <button onClick={() => { setAddAirportFor(area.id); setAirportForm({ name: "", airport_fee: 2.0, polygon: [] }); setAirportMapKey(k => k + 1); }}
                                className="flex items-center gap-2 bg-blue-500 text-white px-4 py-2 rounded-xl text-sm font-semibold hover:bg-blue-600">
                                <Plane className="h-4 w-4" /> Add Airport Zone
                              </button>
                            )}
                          </div>

                          {/* Add Airport Form */}
                          {addAirportFor === area.id && (
                            <div className="bg-blue-50 border border-blue-200 rounded-xl p-5 mb-5">
                              <h5 className="font-bold text-blue-900 mb-3 flex items-center gap-2">
                                <Plane className="h-4 w-4" /> New Airport Zone in {area.name}
                              </h5>
                              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                                <div>
                                  <label className="block text-xs font-semibold text-blue-800 mb-1">Airport Zone Name *</label>
                                  <input className="w-full border border-blue-200 rounded-lg px-3 py-2 text-sm bg-white"
                                    value={airportForm.name}
                                    onChange={e => setAirportForm({ ...airportForm, name: e.target.value })}
                                    placeholder={`e.g. ${area.city || area.name} Airport`} />
                                </div>
                                <div>
                                  <label className="block text-xs font-semibold text-blue-800 mb-1">Airport Fee ($)</label>
                                  <input className="w-full border border-blue-200 rounded-lg px-3 py-2 text-sm bg-white"
                                    type="number" step="0.50" min="0"
                                    value={airportForm.airport_fee}
                                    onChange={e => setAirportForm({ ...airportForm, airport_fee: parseFloat(e.target.value) || 0 })} />
                                </div>
                              </div>
                              <div className="mb-4">
                                <label className="block text-xs font-semibold text-blue-800 mb-2">
                                  Draw Airport Boundary on Map {airportForm.polygon.length === 0 && <span className="text-red-500">(required)</span>}
                                </label>
                                <div className="h-64 rounded-xl overflow-hidden border border-blue-200">
                                  <Suspense fallback={<div className="h-full bg-gray-100 flex items-center justify-center text-gray-400">Loading map...</div>}>
                                    <GeofenceMap
                                      key={airportMapKey}
                                      polygon={airportForm.polygon}
                                      center={getAreaCenter(area)}
                                      zoom={12}
                                      onPolygonChange={(p: any) => setAirportForm({ ...airportForm, polygon: p })}
                                    />
                                  </Suspense>
                                </div>
                                {airportForm.polygon.length > 0 && <p className="text-xs text-green-600 mt-1">{airportForm.polygon.length} points defined</p>}
                              </div>
                              <div className="flex gap-3">
                                <button onClick={() => handleCreateAirportSubRegion(area.id)} className="bg-blue-500 text-white px-5 py-2 rounded-xl text-sm font-semibold hover:bg-blue-600">Create Airport Zone</button>
                                <button onClick={() => setAddAirportFor(null)} className="bg-gray-100 text-gray-600 px-5 py-2 rounded-xl text-sm font-semibold">Cancel</button>
                              </div>
                            </div>
                          )}

                          {/* Existing Sub-regions */}
                          {subRegions.length === 0 && addAirportFor !== area.id ? (
                            <div className="text-center py-10 bg-gray-50 rounded-xl">
                              <Plane className="h-10 w-10 text-gray-300 mx-auto mb-3" />
                              <p className="text-gray-500 font-medium">No airport zones yet</p>
                              <p className="text-gray-400 text-sm mt-1">Add an airport zone to automatically charge a surcharge for rides to/from the airport</p>
                            </div>
                          ) : (
                            <div className="space-y-4">
                              {subRegions.map((sub: any) => (
                                <div key={sub.id} className="bg-blue-50 border border-blue-100 rounded-xl p-4">
                                  <div className="flex items-center justify-between mb-3">
                                    <div className="flex items-center gap-2">
                                      <Plane className="h-4 w-4 text-blue-600" />
                                      <span className="font-bold text-blue-900">{sub.name}</span>
                                      <span className="px-2 py-0.5 bg-blue-200 text-blue-800 text-xs font-bold rounded-md">AIRPORT</span>
                                      {!sub.is_active && <span className="px-2 py-0.5 bg-gray-200 text-gray-600 text-xs font-bold rounded-md">INACTIVE</span>}
                                    </div>
                                    <button onClick={() => handleDelete(sub.id, sub.name)} className="text-sm text-red-500 hover:underline">Delete</button>
                                  </div>
                                  <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">
                                    <FieldInput label="Zone Name" value={sub.name} onSave={v => handleFieldUpdate(sub.id, 'name', v)} />
                                    <FieldInput label="Airport Fee ($)" value={sub.airport_fee || 0} type="number" onSave={v => handleFieldUpdate(sub.id, 'airport_fee', parseFloat(v))} />
                                    <FieldToggle label="Active" value={sub.is_active} onSave={v => handleFieldUpdate(sub.id, 'is_active', v)} />
                                  </div>
                                  {/* Airport zone boundary map */}
                                  <div>
                                    <label className="block text-xs font-semibold text-blue-800 mb-2">Airport Zone Boundary</label>
                                    <div className="h-56 rounded-xl overflow-hidden border border-blue-200">
                                      <Suspense fallback={<div className="h-full bg-gray-100 flex items-center justify-center text-gray-400">Loading map...</div>}>
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
                                      <p className="text-xs text-green-600 mt-1">{getAreaPolygon(sub).length} boundary points</p>
                                    )}
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ─── Inline Editable Field Components ───

function FieldInput({ label, value, type = "text", onSave }: { label: string; value: any; type?: string; onSave: (v: string) => void }) {
  const [val, setVal] = useState(String(value));
  const [dirty, setDirty] = useState(false);
  useEffect(() => { setVal(String(value)); setDirty(false); }, [value]);
  return (
    <div>
      <label className="block text-xs font-semibold text-gray-500 mb-1">{label}</label>
      <div className="flex gap-2">
        <input className="flex-1 border rounded-lg px-3 py-2 text-sm" type={type} step={type === 'number' ? '0.01' : undefined} value={val}
          onChange={e => { setVal(e.target.value); setDirty(true); }} />
        {dirty && <button onClick={() => { onSave(val); setDirty(false); }} className="px-3 py-1 bg-red-500 text-white text-xs rounded-lg font-semibold">Save</button>}
      </div>
    </div>
  );
}

function FieldSelect({ label, value, options, onSave }: { label: string; value: string; options: string[]; onSave: (v: string) => void }) {
  return (
    <div>
      <label className="block text-xs font-semibold text-gray-500 mb-1">{label}</label>
      <select className="w-full border rounded-lg px-3 py-2 text-sm" value={value} onChange={e => onSave(e.target.value)}>
        {options.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    </div>
  );
}

function FieldToggle({ label, value, onSave }: { label: string; value: boolean; onSave: (v: boolean) => void }) {
  return (
    <div className="flex items-center gap-2">
      <label className="text-xs font-semibold text-gray-500">{label}</label>
      <button onClick={() => onSave(!value)}>
        {value ? <ToggleRight className="h-6 w-6 text-green-500" /> : <ToggleLeft className="h-6 w-6 text-gray-300" />}
      </button>
    </div>
  );
}

// ─── Vehicle Pricing Editor ───

function VehiclePricingEditor({ pricing, onSave }: { pricing: any[]; onSave: (p: any[]) => void }) {
  const [rows, setRows] = useState(pricing.length > 0 ? pricing : [
    { vehicle_type: 'Economy', base_fare: 3.50, per_km: 1.20, per_min: 0.25, min_fare: 7.00, booking_fee: 2.00 },
    { vehicle_type: 'Premium', base_fare: 5.00, per_km: 2.00, per_min: 0.40, min_fare: 12.00, booking_fee: 2.50 },
    { vehicle_type: 'XL', base_fare: 6.00, per_km: 2.50, per_min: 0.50, min_fare: 15.00, booking_fee: 3.00 },
    { vehicle_type: 'Van', base_fare: 7.00, per_km: 3.00, per_min: 0.60, min_fare: 18.00, booking_fee: 3.50 },
  ]);

  const update = (idx: number, field: string, val: string) => {
    const next = [...rows];
    next[idx] = { ...next[idx], [field]: field === 'vehicle_type' ? val : parseFloat(val) || 0 };
    setRows(next);
  };

  const addRow = () => setRows([...rows, { vehicle_type: '', base_fare: 0, per_km: 0, per_min: 0, min_fare: 0, booking_fee: 0 }]);
  const removeRow = (i: number) => setRows(rows.filter((_, idx) => idx !== i));

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-gray-500 border-b">
              <th className="pb-2 pr-2">Vehicle Type</th>
              <th className="pb-2 pr-2">Base Fare ($)</th>
              <th className="pb-2 pr-2">Per KM ($)</th>
              <th className="pb-2 pr-2">Per Min ($)</th>
              <th className="pb-2 pr-2">Min Fare ($)</th>
              <th className="pb-2 pr-2">Booking Fee ($)</th>
              <th className="pb-2"></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-b">
                {['vehicle_type', 'base_fare', 'per_km', 'per_min', 'min_fare', 'booking_fee'].map(f => (
                  <td key={f} className="py-2 pr-2">
                    <input className="w-full border rounded-lg px-2 py-1.5 text-sm"
                      type={f === 'vehicle_type' ? 'text' : 'number'} step="0.01"
                      value={(r as any)[f]} onChange={e => update(i, f, e.target.value)} />
                  </td>
                ))}
                <td className="py-2"><button onClick={() => removeRow(i)} className="text-gray-400 hover:text-red-500"><Trash2 className="h-4 w-4" /></button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex gap-3 mt-3">
        <button onClick={addRow} className="text-sm text-red-500 font-semibold hover:underline">+ Add vehicle type</button>
        <button onClick={() => onSave(rows)} className="bg-red-500 text-white px-5 py-2 rounded-xl text-sm font-semibold hover:bg-red-600">Save Pricing</button>
      </div>
    </div>
  );
}

// ─── Documents Editor ───

function DocumentsEditor({ docs, onSave }: { docs: any[]; onSave: (d: any[]) => void }) {
  const [rows, setRows] = useState(docs.length > 0 ? docs : [
    { key: 'drivers_license',       label: "Driver's License",    has_expiry: true,  required: true, requires_back_side: false },
    { key: 'vehicle_insurance',     label: 'Vehicle Insurance',   has_expiry: true,  required: true, requires_back_side: false },
    { key: 'vehicle_registration',  label: 'Vehicle Registration',has_expiry: true,  required: true, requires_back_side: false },
    { key: 'background_check',      label: 'Background Check',    has_expiry: true,  required: true, requires_back_side: false },
    { key: 'vehicle_inspection',    label: 'Vehicle Inspection',  has_expiry: true,  required: true, requires_back_side: false },
  ]);
  const [editingIdx, setEditingIdx] = useState<number | null>(null);
  const [dirty, setDirty] = useState(false);

  const update = (idx: number, field: string, val: any) => {
    const next = [...rows];
    next[idx] = { ...next[idx], [field]: val };
    setRows(next);
    setDirty(true);
  };

  const addDoc = () => {
    setRows([...rows, { key: '', label: '', has_expiry: false, required: true, requires_back_side: false }]);
    setEditingIdx(rows.length);
    setDirty(true);
  };

  const removeDoc = (i: number) => {
    setRows(rows.filter((_, idx) => idx !== i));
    setEditingIdx(null);
    setDirty(true);
  };

  const moveDoc = (from: number, to: number) => {
    if (to < 0 || to >= rows.length) return;
    const next = [...rows];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    setRows(next);
    setEditingIdx(to);
    setDirty(true);
  };

  const requiredCount = rows.filter(r => r.required !== false).length;
  const expiryCount = rows.filter(r => r.has_expiry).length;

  return (
    <div>
      {/* Header */}
      <div className="flex items-start justify-between mb-5">
        <div>
          <h4 className="font-bold text-gray-800 text-base">Required Documents</h4>
          <p className="text-sm text-gray-500 mt-0.5">Define which documents drivers must upload to operate in this area.</p>
        </div>
        <div className="flex items-center gap-4 text-xs text-gray-500">
          <span className="flex items-center gap-1"><FileText className="h-3.5 w-3.5" /> {rows.length} total</span>
          <span className="flex items-center gap-1"><ShieldCheck className="h-3.5 w-3.5 text-emerald-500" /> {requiredCount} required</span>
          <span className="flex items-center gap-1"><Clock className="h-3.5 w-3.5 text-amber-500" /> {expiryCount} with expiry</span>
        </div>
      </div>

      {/* Document Cards Grid */}
      {rows.length === 0 ? (
        <div className="text-center py-12 bg-gray-50 rounded-xl border-2 border-dashed border-gray-200">
          <Image className="h-10 w-10 mx-auto mb-3 text-gray-300" />
          <p className="text-sm font-medium text-gray-500">No documents required</p>
          <p className="text-xs text-gray-400 mt-1">Add document types that drivers need to upload</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {rows.map((r, i) => {
            const isEditing = editingIdx === i;
            return (
              <div key={i} className={`rounded-xl border overflow-hidden transition-all ${isEditing ? 'ring-2 ring-red-200 border-red-300 shadow-md' : 'bg-white hover:shadow-sm border-gray-200'}`}>
                {/* Card Header with preview */}
                <div className={`px-4 py-3 flex items-center gap-3 ${isEditing ? 'bg-red-50' : 'bg-gray-50'}`}>
                  <div className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 ${r.required !== false ? 'bg-emerald-100' : 'bg-gray-100'}`}>
                    <FileText className={`h-5 w-5 ${r.required !== false ? 'text-emerald-600' : 'text-gray-400'}`} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-bold text-gray-800 truncate">{r.label || 'Untitled Document'}</p>
                    <p className="text-xs text-gray-400 font-mono">{r.key || 'no_key'}</p>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button onClick={() => moveDoc(i, i - 1)} disabled={i === 0} className="p-1 text-gray-300 hover:text-gray-500 disabled:opacity-30" title="Move up">
                      <ChevronUp className="h-4 w-4" />
                    </button>
                    <button onClick={() => moveDoc(i, i + 1)} disabled={i === rows.length - 1} className="p-1 text-gray-300 hover:text-gray-500 disabled:opacity-30" title="Move down">
                      <ChevronDown className="h-4 w-4" />
                    </button>
                  </div>
                </div>

                {/* Status badges */}
                <div className="px-4 py-2.5 flex items-center gap-2 flex-wrap">
                  {r.required !== false ? (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-100 text-emerald-700"><ShieldCheck className="h-3 w-3" /> Required</span>
                  ) : (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-gray-100 text-gray-500"><ShieldAlert className="h-3 w-3" /> Optional</span>
                  )}
                  {r.has_expiry ? (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-100 text-amber-700"><Clock className="h-3 w-3" /> Has Expiry</span>
                  ) : (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-gray-100 text-gray-400">No Expiry</span>
                  )}
                  {r.requires_back_side && (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-blue-100 text-blue-700"><Image className="h-3 w-3" /> Both Sides</span>
                  )}
                </div>

                {/* Expand to edit or show actions */}
                {isEditing ? (
                  <div className="px-4 pb-4 space-y-3 border-t bg-white">
                    <div className="pt-3">
                      <label className="block text-[11px] font-semibold text-gray-500 mb-1">Document Label</label>
                      <input className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-red-200 focus:border-red-300 outline-none" placeholder="e.g. Driver's License" value={r.label} onChange={e => update(i, 'label', e.target.value)} />
                    </div>
                    <div>
                      <label className="block text-[11px] font-semibold text-gray-500 mb-1">Key (identifier)</label>
                      <input className="w-full border rounded-lg px-3 py-2 text-sm font-mono focus:ring-2 focus:ring-red-200 focus:border-red-300 outline-none" placeholder="e.g. drivers_license" value={r.key} onChange={e => update(i, 'key', e.target.value)} />
                    </div>
                    <div className="flex flex-col gap-2.5">
                      <label className="flex items-center gap-2 text-sm cursor-pointer">
                        <input type="checkbox" checked={r.has_expiry} onChange={e => update(i, 'has_expiry', e.target.checked)} className="accent-red-500 w-4 h-4" />
                        <span>Requires expiry date</span>
                      </label>
                      <label className="flex items-center gap-2 text-sm cursor-pointer">
                        <input type="checkbox" checked={r.required !== false} onChange={e => update(i, 'required', e.target.checked)} className="accent-red-500 w-4 h-4" />
                        <span>Required</span>
                      </label>
                      <label className="flex items-center gap-2 text-sm cursor-pointer">
                        <input type="checkbox" checked={!!r.requires_back_side} onChange={e => update(i, 'requires_back_side', e.target.checked)} className="accent-red-500 w-4 h-4" />
                        <span>Requires both sides <span className="text-xs text-gray-400">(front &amp; back photo)</span></span>
                      </label>
                    </div>
                    <div className="flex items-center justify-between pt-2">
                      <button onClick={() => removeDoc(i)} className="flex items-center gap-1 text-sm text-red-500 hover:text-red-700 font-medium">
                        <Trash2 className="h-3.5 w-3.5" /> Remove
                      </button>
                      <button onClick={() => setEditingIdx(null)} className="px-4 py-1.5 bg-gray-100 text-gray-700 text-sm rounded-lg font-semibold hover:bg-gray-200">Done</button>
                    </div>
                  </div>
                ) : (
                  <div className="px-4 pb-3 flex items-center justify-between">
                    <button onClick={() => setEditingIdx(i)} className="flex items-center gap-1 text-xs text-gray-400 hover:text-red-500 font-medium">
                      <Pencil className="h-3 w-3" /> Edit
                    </button>
                    <button onClick={() => removeDoc(i)} className="flex items-center gap-1 text-xs text-gray-300 hover:text-red-500">
                      <Trash2 className="h-3 w-3" /> Remove
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center gap-3 mt-5 pt-4 border-t">
        <button onClick={addDoc} className="flex items-center gap-1.5 text-sm text-red-500 font-semibold hover:text-red-700 transition">
          <Plus className="h-4 w-4" /> Add document type
        </button>
        <div className="flex-1" />
        {dirty && <span className="text-xs text-amber-600 font-medium">Unsaved changes</span>}
        <button onClick={() => { onSave(rows); setDirty(false); }} className={`px-6 py-2.5 rounded-xl text-sm font-semibold transition ${dirty ? 'bg-red-500 text-white hover:bg-red-600 shadow-sm' : 'bg-gray-100 text-gray-400'}`}>
          <span className="flex items-center gap-1.5"><CheckCircle className="h-4 w-4" /> Save Documents</span>
        </button>
      </div>
    </div>
  );
}

// ─── Area Fees Editor ───

function AreaFeesEditor({ areaId, area, fees, loading, onReload, onFieldUpdate }: {
    areaId: string; area: any; fees: any[]; loading: boolean;
    onReload: () => void; onFieldUpdate: (areaId: string, field: string, value: any) => void;
}) {
    const [editingFee, setEditingFee] = useState<any>(null);
    const [saving, setSaving] = useState(false);

    const FEE_TYPES = [
        { value: 'custom', label: 'Custom Fee' },
        { value: 'airport', label: 'Airport Fee' },
        { value: 'night', label: 'Night Surcharge' },
        { value: 'toll', label: 'Toll Fee' },
        { value: 'event', label: 'Event Fee' },
        { value: 'holiday', label: 'Holiday Fee' },
    ];
    const CALC_MODES = [
        { value: 'flat', label: 'Flat ($)' },
        { value: 'per_km', label: 'Per KM ($)' },
        { value: 'percentage', label: 'Percentage (%)' },
    ];

    const handleCreate = async () => {
        setSaving(true);
        try {
            await createAreaFee(areaId, { fee_name: 'New Fee', fee_type: 'custom', calc_mode: 'flat', amount: 0, is_active: true });
            onReload();
        } catch (e: any) { alert(e?.message || 'Failed'); }
        setSaving(false);
    };

    const handleUpdate = async (feeId: string, data: any) => {
        try { await updateAreaFee(areaId, feeId, data); onReload(); } catch (e: any) { alert(e?.message || 'Failed'); }
    };

    const handleDelete = async (feeId: string) => {
        if (!confirm('Delete this fee?')) return;
        try { await deleteAreaFee(areaId, feeId); onReload(); } catch (e: any) { alert(e?.message || 'Failed'); }
    };

    return (
        <div className="space-y-6">
            {/* SECTION 1: Area Fees */}
            <div>
                <div className="flex items-center justify-between mb-4">
                    <div>
                        <h4 className="font-bold text-gray-800">Area Fees</h4>
                        <p className="text-sm text-gray-500">Fees applied to rides in this area. Calculated dynamically based on type.</p>
                    </div>
                    <button onClick={handleCreate} disabled={saving}
                        className="flex items-center gap-1.5 bg-red-500 text-white px-4 py-2 rounded-xl text-sm font-semibold hover:bg-red-600 disabled:opacity-50">
                        <Plus className="h-4 w-4" /> Add Fee
                    </button>
                </div>

                {loading ? (
                    <div className="text-center py-8 text-gray-400">Loading fees...</div>
                ) : fees.length === 0 ? (
                    <div className="text-center py-8 bg-gray-50 rounded-xl border-2 border-dashed">
                        <DollarSign className="h-8 w-8 text-gray-300 mx-auto mb-2" />
                        <p className="text-sm text-gray-500">No fees configured</p>
                        <p className="text-xs text-gray-400">Add fees like airport surcharge, night fee, city fee, etc.</p>
                    </div>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                        {fees.map(fee => (
                            <div key={fee.id} className={`rounded-xl border p-4 ${fee.is_active ? 'bg-white' : 'bg-gray-50 opacity-60'}`}>
                                <div className="flex items-start justify-between mb-3">
                                    <div>
                                        <p className="font-bold text-gray-800">{fee.fee_name || fee.fee_type || 'Fee'}</p>
                                        <p className="text-xs text-gray-400 mt-0.5">{fee.fee_type} · {fee.calc_mode || 'flat'}</p>
                                    </div>
                                    <div className="text-right">
                                        <p className="text-lg font-bold text-gray-900">
                                            {fee.calc_mode === 'percentage' ? `${fee.amount}%` : `$${Number(fee.amount || 0).toFixed(2)}`}
                                            {fee.calc_mode === 'per_km' && '/km'}
                                        </p>
                                    </div>
                                </div>
                                {fee.description && <p className="text-xs text-gray-500 mb-3">{fee.description}</p>}
                                {fee.conditions && fee.conditions.start_hour !== undefined && (
                                    <p className="text-xs text-gray-400 mb-3">Hours: {fee.conditions.start_hour}:00 - {fee.conditions.end_hour}:00</p>
                                )}
                                <div className="flex items-center gap-2 pt-2 border-t">
                                    <button onClick={() => setEditingFee(editingFee?.id === fee.id ? null : fee)} className="text-xs text-gray-400 hover:text-red-500 font-medium flex items-center gap-1">
                                        <Pencil className="h-3 w-3" /> Edit
                                    </button>
                                    <button onClick={() => handleUpdate(fee.id, { is_active: !fee.is_active })} className="text-xs text-gray-400 hover:text-gray-600 font-medium ml-auto">
                                        {fee.is_active ? 'Disable' : 'Enable'}
                                    </button>
                                    <button onClick={() => handleDelete(fee.id)} className="text-xs text-gray-300 hover:text-red-500 font-medium">
                                        <Trash2 className="h-3 w-3" />
                                    </button>
                                </div>
                                {editingFee?.id === fee.id && (
                                    <FeeEditForm fee={editingFee} feeTypes={FEE_TYPES} calcModes={CALC_MODES}
                                        onSave={async (data) => { await handleUpdate(fee.id, data); setEditingFee(null); }}
                                        onCancel={() => setEditingFee(null)} />
                                )}
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* SECTION 2: Taxes */}
            <div>
                <h4 className="font-bold text-gray-800 mb-3">Tax Configuration</h4>
                <div className="bg-white rounded-xl border p-4">
                    <div className="flex items-center gap-4 mb-4">
                        <label className="flex items-center gap-2 text-sm">
                            <input type="radio" name={`tax-${areaId}`} checked={!area.hst_enabled}
                                onChange={() => onFieldUpdate(areaId, 'hst_enabled', false)} className="accent-red-500" />
                            GST + PST (separate)
                        </label>
                        <label className="flex items-center gap-2 text-sm">
                            <input type="radio" name={`tax-${areaId}`} checked={!!area.hst_enabled}
                                onChange={() => onFieldUpdate(areaId, 'hst_enabled', true)} className="accent-red-500" />
                            HST (harmonized)
                        </label>
                    </div>
                    {area.hst_enabled ? (
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <FieldInput label="HST Rate (%)" value={area.hst_rate || 0} type="number"
                                onSave={v => onFieldUpdate(areaId, 'hst_rate', parseFloat(v))} />
                        </div>
                    ) : (
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <div className="flex items-center gap-2 mb-2">
                                    <FieldToggle label="GST Enabled" value={area.gst_enabled !== false}
                                        onSave={v => onFieldUpdate(areaId, 'gst_enabled', v)} />
                                </div>
                                {area.gst_enabled !== false && (
                                    <FieldInput label="GST Rate (%)" value={area.gst_rate || 5} type="number"
                                        onSave={v => onFieldUpdate(areaId, 'gst_rate', parseFloat(v))} />
                                )}
                            </div>
                            <div>
                                <div className="flex items-center gap-2 mb-2">
                                    <FieldToggle label="PST Enabled" value={!!area.pst_enabled}
                                        onSave={v => onFieldUpdate(areaId, 'pst_enabled', v)} />
                                </div>
                                {area.pst_enabled && (
                                    <FieldInput label="PST Rate (%)" value={area.pst_rate || 0} type="number"
                                        onSave={v => onFieldUpdate(areaId, 'pst_rate', parseFloat(v))} />
                                )}
                            </div>
                        </div>
                    )}
                </div>
            </div>

            {/* SECTION 3: Cancellation Fees */}
            <div>
                <h4 className="font-bold text-gray-800 mb-3">Cancellation Fees</h4>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <FieldInput label="Rider cancel — driver on the way ($)" value={area.rider_cancel_fee_before_driver || 0} type="number" onSave={v => onFieldUpdate(areaId, 'rider_cancel_fee_before_driver', parseFloat(v))} />
                    <FieldInput label="Rider cancel — driver arrived ($)" value={area.rider_cancel_fee_after_arrival || 4.50} type="number" onSave={v => onFieldUpdate(areaId, 'rider_cancel_fee_after_arrival', parseFloat(v))} />
                    <FieldInput label="Driver gets ($)" value={area.cancel_fee_driver_share || 4.00} type="number" onSave={v => onFieldUpdate(areaId, 'cancel_fee_driver_share', parseFloat(v))} />
                    <FieldInput label="Admin gets ($)" value={area.cancel_fee_admin_share || 0.50} type="number" onSave={v => onFieldUpdate(areaId, 'cancel_fee_admin_share', parseFloat(v))} />
                    <FieldInput label="Driver cancel penalty ($)" value={area.driver_cancel_fee || 0} type="number" onSave={v => onFieldUpdate(areaId, 'driver_cancel_fee', parseFloat(v))} />
                    <FieldInput label="Free cancel window (sec)" value={area.free_cancel_window_seconds || 120} type="number" onSave={v => onFieldUpdate(areaId, 'free_cancel_window_seconds', parseInt(v))} />
                </div>
            </div>
        </div>
    );
}

function FeeEditForm({ fee, feeTypes, calcModes, onSave, onCancel }: {
    fee: any; feeTypes: { value: string; label: string }[]; calcModes: { value: string; label: string }[];
    onSave: (data: any) => Promise<void>; onCancel: () => void;
}) {
    const [form, setForm] = useState({
        fee_name: fee.fee_name || '', fee_type: fee.fee_type || 'custom',
        calc_mode: fee.calc_mode || 'flat', amount: fee.amount || 0,
        description: fee.description || '', conditions: fee.conditions || {},
    });
    const [saving, setSaving] = useState(false);

    return (
        <div className="mt-3 pt-3 border-t space-y-3">
            <div className="grid grid-cols-2 gap-3">
                <div>
                    <label className="block text-xs font-semibold text-gray-500 mb-1">Fee Name</label>
                    <input className="w-full border rounded-lg px-3 py-2 text-sm" value={form.fee_name}
                        onChange={e => setForm({ ...form, fee_name: e.target.value })} />
                </div>
                <div>
                    <label className="block text-xs font-semibold text-gray-500 mb-1">Fee Type</label>
                    <select className="w-full border rounded-lg px-3 py-2 text-sm" value={form.fee_type}
                        onChange={e => setForm({ ...form, fee_type: e.target.value })}>
                        {feeTypes.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                    </select>
                </div>
                <div>
                    <label className="block text-xs font-semibold text-gray-500 mb-1">Calculation Mode</label>
                    <select className="w-full border rounded-lg px-3 py-2 text-sm" value={form.calc_mode}
                        onChange={e => setForm({ ...form, calc_mode: e.target.value })}>
                        {calcModes.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
                    </select>
                </div>
                <div>
                    <label className="block text-xs font-semibold text-gray-500 mb-1">
                        Amount {form.calc_mode === 'percentage' ? '(%)' : form.calc_mode === 'per_km' ? '($/km)' : '($)'}
                    </label>
                    <input className="w-full border rounded-lg px-3 py-2 text-sm" type="number" step="0.01"
                        value={form.amount} onChange={e => setForm({ ...form, amount: parseFloat(e.target.value) || 0 })} />
                </div>
            </div>
            <div>
                <label className="block text-xs font-semibold text-gray-500 mb-1">Description (optional)</label>
                <input className="w-full border rounded-lg px-3 py-2 text-sm" value={form.description}
                    onChange={e => setForm({ ...form, description: e.target.value })} />
            </div>
            {form.fee_type === 'night' && (
                <div className="grid grid-cols-2 gap-3">
                    <div>
                        <label className="block text-xs font-semibold text-gray-500 mb-1">Start Hour (0-23)</label>
                        <input className="w-full border rounded-lg px-3 py-2 text-sm" type="number" min="0" max="23"
                            value={form.conditions.start_hour ?? 23}
                            onChange={e => setForm({ ...form, conditions: { ...form.conditions, start_hour: parseInt(e.target.value) } })} />
                    </div>
                    <div>
                        <label className="block text-xs font-semibold text-gray-500 mb-1">End Hour (0-23)</label>
                        <input className="w-full border rounded-lg px-3 py-2 text-sm" type="number" min="0" max="23"
                            value={form.conditions.end_hour ?? 5}
                            onChange={e => setForm({ ...form, conditions: { ...form.conditions, end_hour: parseInt(e.target.value) } })} />
                    </div>
                </div>
            )}
            <div className="flex gap-2 pt-1">
                <button onClick={async () => { setSaving(true); await onSave(form); setSaving(false); }} disabled={saving}
                    className="bg-red-500 text-white px-4 py-1.5 rounded-lg text-sm font-semibold hover:bg-red-600 disabled:opacity-50">
                    {saving ? 'Saving...' : 'Save'}
                </button>
                <button onClick={onCancel} className="bg-gray-100 text-gray-600 px-4 py-1.5 rounded-lg text-sm font-semibold">Cancel</button>
            </div>
        </div>
    );
}

// --- Spinr Pass per-area tab with full plan management ---

const DURATION_OPTIONS = [
  { label: "Daily", value: 1 },
  { label: "Weekly", value: 7 },
  { label: "Monthly", value: 30 },
  { label: "Yearly", value: 365 },
];

function SpinrPassAreaTab({ area, plans, onToggle, onPlansChanged }: {
  area: any; plans: any[]; onToggle: (v: boolean) => void; onPlansChanged: () => void;
}) {
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [subs, setSubs] = useState<any[]>([]);
  const [subsLoaded, setSubsLoaded] = useState(false);
  const [form, setForm] = useState({ name: "", price: "", duration_days: 30, rides_per_day: -1, description: "", features: "", is_active: true });

  const loadSubs = async () => {
    try { const s = await getDriverSubscriptions(); setSubs(s || []); } catch {}
    setSubsLoaded(true);
  };

  const resetForm = () => { setShowForm(false); setEditingId(null); setForm({ name: "", price: "", duration_days: 30, rides_per_day: -1, description: "", features: "", is_active: true }); };

  const handleSubmit = async () => {
    if (!form.name || !form.price) return;
    const data = {
      name: form.name, price: parseFloat(form.price), duration_days: form.duration_days,
      rides_per_day: form.rides_per_day, description: form.description,
      features: form.features ? form.features.split(",").map(f => f.trim()).filter(Boolean) : [],
      is_active: form.is_active,
    };
    try {
      if (editingId) { await updateSubscriptionPlan(editingId, data); }
      else { await createSubscriptionPlan(data); }
      resetForm(); onPlansChanged();
    } catch (e: any) { alert(e?.message || "Failed to save plan"); }
  };

  const handleEdit = (p: any) => {
    setEditingId(p.id);
    setForm({ name: p.name, price: String(p.price), duration_days: p.duration_days, rides_per_day: p.rides_per_day, description: p.description || "", features: (p.features || []).join(", "), is_active: p.is_active });
    setShowForm(true);
  };

  const handleDeletePlan = async (p: any) => {
    if (!confirm(`Delete "${p.name}" plan?`)) return;
    await deleteSubscriptionPlan(p.id); onPlansChanged();
  };

  const handleTogglePlan = async (p: any) => {
    await updateSubscriptionPlan(p.id, { is_active: !p.is_active }); onPlansChanged();
  };

  const getDurationLabel = (days: number) => DURATION_OPTIONS.find(d => d.value === days)?.label || `${days} days`;

  const enabled = area.spinr_pass_enabled !== false;

  return (
    <div>
      {/* Kill switch */}
      <div className={`flex items-center justify-between p-4 rounded-xl mb-5 ${enabled ? 'bg-green-50 border border-green-200' : 'bg-gray-50 border border-gray-200'}`}>
        <div>
          <h4 className="font-bold text-gray-800">Spinr Pass for {area.name}</h4>
          <p className="text-sm text-gray-500">
            {enabled ? 'Drivers in this area can see and subscribe to plans' : 'Disabled — drivers see "It\'s Free Right Now!" instead'}
          </p>
        </div>
        <FieldToggle label={enabled ? "ON" : "OFF"} value={enabled} onSave={onToggle} />
      </div>

      {/* Plan management */}
      <div className="flex items-center justify-between mb-4">
        <h4 className="font-bold text-gray-800">Subscription Plans</h4>
        <button onClick={() => { resetForm(); setShowForm(true); }} className="flex items-center gap-1.5 bg-red-500 text-white px-4 py-2 rounded-xl text-sm font-semibold hover:bg-red-600">
          <Plus className="h-4 w-4" /> New Plan
        </button>
      </div>

      {/* Create/Edit Form */}
      {showForm && (
        <div className="bg-white rounded-xl border p-5 mb-5 shadow-sm">
          <h5 className="font-bold mb-3">{editingId ? "Edit Plan" : "New Plan"}</h5>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">
            <div>
              <label className="block text-xs font-semibold text-gray-500 mb-1">Plan Name *</label>
              <input className="w-full border rounded-lg px-3 py-2 text-sm" placeholder="e.g. Basic" value={form.name} onChange={e => setForm({...form, name: e.target.value})} />
            </div>
            <div>
              <label className="block text-xs font-semibold text-gray-500 mb-1">Price (CAD) *</label>
              <input className="w-full border rounded-lg px-3 py-2 text-sm" type="number" step="0.01" placeholder="19.99" value={form.price} onChange={e => setForm({...form, price: e.target.value})} />
            </div>
            <div>
              <label className="block text-xs font-semibold text-gray-500 mb-1">Duration</label>
              <select className="w-full border rounded-lg px-3 py-2 text-sm" value={form.duration_days} onChange={e => setForm({...form, duration_days: parseInt(e.target.value)})}>
                {DURATION_OPTIONS.map(d => <option key={d.value} value={d.value}>{d.label}</option>)}
              </select>
            </div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
            <div>
              <label className="block text-xs font-semibold text-gray-500 mb-1">Rides Per Day</label>
              <div className="flex gap-1.5 flex-wrap">
                <button onClick={() => setForm({...form, rides_per_day: -1})} className={`px-3 py-1.5 rounded-lg text-xs font-semibold border ${form.rides_per_day === -1 ? "bg-red-500 text-white border-red-500" : "bg-white border-gray-200"}`}>Unlimited</button>
                {[4, 8, 12, 20].map(n => (
                  <button key={n} onClick={() => setForm({...form, rides_per_day: n})} className={`px-3 py-1.5 rounded-lg text-xs font-semibold border ${form.rides_per_day === n ? "bg-red-500 text-white border-red-500" : "bg-white border-gray-200"}`}>{n}</button>
                ))}
              </div>
            </div>
            <div>
              <label className="block text-xs font-semibold text-gray-500 mb-1">Description</label>
              <input className="w-full border rounded-lg px-3 py-2 text-sm" placeholder="Brief description" value={form.description} onChange={e => setForm({...form, description: e.target.value})} />
            </div>
          </div>
          <div className="mb-3">
            <label className="block text-xs font-semibold text-gray-500 mb-1">Features (comma-separated)</label>
            <input className="w-full border rounded-lg px-3 py-2 text-sm" placeholder="Priority support, Surge protection" value={form.features} onChange={e => setForm({...form, features: e.target.value})} />
          </div>
          <div className="flex items-center gap-4 mb-4">
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.is_active} onChange={e => setForm({...form, is_active: e.target.checked})} className="accent-red-500" /> Active</label>
          </div>
          <div className="flex gap-2">
            <button onClick={handleSubmit} className="bg-red-500 text-white px-5 py-2 rounded-xl text-sm font-semibold hover:bg-red-600">{editingId ? "Save" : "Create Plan"}</button>
            <button onClick={resetForm} className="bg-gray-100 text-gray-600 px-5 py-2 rounded-xl text-sm font-semibold">Cancel</button>
          </div>
        </div>
      )}

      {/* Plans List */}
      {plans.length === 0 ? (
        <div className="text-center py-12 bg-gray-50 rounded-xl">
          <CreditCard className="h-10 w-10 text-gray-300 mx-auto mb-3" />
          <p className="text-gray-500 font-medium">No subscription plans yet</p>
          <p className="text-gray-400 text-sm mt-1">Create your first Spinr Pass plan above</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 mb-6">
          {plans.map(p => (
            <div key={p.id} className={`bg-white rounded-xl border p-5 relative ${!p.is_active ? "opacity-50" : ""}`}>
              <button onClick={() => handleTogglePlan(p)} className="absolute top-3 right-3">
                {p.is_active ? <ToggleRight className="h-5 w-5 text-green-500" /> : <ToggleLeft className="h-5 w-5 text-gray-300" />}
              </button>
              <h5 className="font-bold text-gray-900 text-lg">{p.name}</h5>
              {p.description && <p className="text-gray-500 text-xs mt-0.5">{p.description}</p>}
              <div className="mt-2 mb-3">
                <span className="text-2xl font-extrabold text-red-500">${p.price?.toFixed(2)}</span>
                <span className="text-gray-400 text-xs ml-1">/ {getDurationLabel(p.duration_days).toLowerCase()}</span>
              </div>
              <p className="text-sm text-gray-600 mb-1">
                {p.rides_per_day === -1 ? 'Unlimited rides/day' : `${p.rides_per_day} rides/day`}
              </p>
              <p className="text-xs text-gray-400">{p.subscriber_count || 0} subscribers</p>
              {(p.features || []).length > 0 && (
                <div className="border-t mt-3 pt-2">
                  {p.features.map((f: string, i: number) => <p key={i} className="text-xs text-gray-500 py-0.5">✓ {f}</p>)}
                </div>
              )}
              <div className="flex gap-2 mt-3 pt-2 border-t">
                <button onClick={() => handleEdit(p)} className="flex-1 text-center py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-50 rounded-lg"><Pencil className="h-3 w-3 inline mr-1" />Edit</button>
                <button onClick={() => handleDeletePlan(p)} className="flex-1 text-center py-1.5 text-xs font-semibold text-red-500 hover:bg-red-50 rounded-lg"><Trash2 className="h-3 w-3 inline mr-1" />Delete</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Subscribers */}
      {plans.length > 0 && (
        <div>
          <div className="flex items-center justify-between mb-3">
            <h4 className="font-bold text-gray-800">Subscribers</h4>
            {!subsLoaded && <button onClick={loadSubs} className="text-sm text-red-500 font-semibold hover:underline">Load subscribers</button>}
          </div>
          {subsLoaded && (
            subs.length === 0 ? (
              <p className="text-gray-400 text-sm">No subscribers yet.</p>
            ) : (
              <div className="bg-white rounded-xl border overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 text-left">
                    <tr>
                      <th className="px-4 py-2 font-semibold text-gray-600 text-xs">Driver</th>
                      <th className="px-4 py-2 font-semibold text-gray-600 text-xs">Plan</th>
                      <th className="px-4 py-2 font-semibold text-gray-600 text-xs">Status</th>
                      <th className="px-4 py-2 font-semibold text-gray-600 text-xs">Expires</th>
                    </tr>
                  </thead>
                  <tbody>
                    {subs.map(s => (
                      <tr key={s.id} className="border-t">
                        <td className="px-4 py-2 font-mono text-xs">{s.driver_id?.slice(0, 8)}...</td>
                        <td className="px-4 py-2">{s.plan_name}</td>
                        <td className="px-4 py-2">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${s.status === 'active' ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'}`}>{s.status?.toUpperCase()}</span>
                        </td>
                        <td className="px-4 py-2 text-xs text-gray-500">{s.expires_at ? new Date(s.expires_at).toLocaleDateString() : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          )}
        </div>
      )}
    </div>
  );
}
