"use client";

import { useEffect, useState, lazy, Suspense } from "react";
import { getServiceAreas, createServiceArea, updateServiceArea, deleteServiceArea, getSubscriptionPlans } from "@/lib/api";
import { Plus, Trash2, Pencil, MapPin, Settings, DollarSign, Car, CreditCard, ChevronDown, ChevronUp, ToggleLeft, ToggleRight, X, FileText, GripVertical, Clock, ShieldCheck, ShieldAlert, CheckCircle, AlertTriangle, Image } from "lucide-react";

const GeofenceMap = lazy(() => import("@/components/geofence-map"));

const CITY_PRESETS: Record<string, { city: string; province: string; center: { lat: number; lng: number }; polygon: { lat: number; lng: number }[] }> = {
  saskatoon: { city: "Saskatoon", province: "SK", center: { lat: 52.13, lng: -106.67 }, polygon: [{ lat: 52.19, lng: -106.75 }, { lat: 52.19, lng: -106.55 }, { lat: 52.08, lng: -106.55 }, { lat: 52.08, lng: -106.75 }] },
  regina: { city: "Regina", province: "SK", center: { lat: 50.45, lng: -104.62 }, polygon: [{ lat: 50.50, lng: -104.72 }, { lat: 50.50, lng: -104.52 }, { lat: 50.40, lng: -104.52 }, { lat: 50.40, lng: -104.72 }] },
  calgary: { city: "Calgary", province: "AB", center: { lat: 51.04, lng: -114.07 }, polygon: [{ lat: 51.15, lng: -114.25 }, { lat: 51.15, lng: -113.90 }, { lat: 50.90, lng: -113.90 }, { lat: 50.90, lng: -114.25 }] },
  edmonton: { city: "Edmonton", province: "AB", center: { lat: 53.55, lng: -113.49 }, polygon: [{ lat: 53.65, lng: -113.65 }, { lat: 53.65, lng: -113.35 }, { lat: 53.45, lng: -113.35 }, { lat: 53.45, lng: -113.65 }] },
  winnipeg: { city: "Winnipeg", province: "MB", center: { lat: 49.90, lng: -97.14 }, polygon: [{ lat: 49.97, lng: -97.30 }, { lat: 49.97, lng: -96.98 }, { lat: 49.80, lng: -96.98 }, { lat: 49.80, lng: -97.30 }] },
};

function polygonToText(polygon: any[]) { return polygon.map(p => `${p.lat}, ${p.lng}`).join("\n"); }

export default function ServiceAreasPage() {
  const [areas, setAreas] = useState<any[]>([]);
  const [plans, setPlans] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [editTab, setEditTab] = useState("general");
  const [showCreate, setShowCreate] = useState(false);

  // Create form
  const [createForm, setCreateForm] = useState({
    name: "", city: "", province: "SK", preset: "",
    polygon: [] as any[], polygonText: "",
    is_active: true, is_airport: false,
  });
  const [mapKey, setMapKey] = useState(0);

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

  const handleFieldUpdate = async (areaId: string, field: string, value: any) => {
    try {
      await updateServiceArea(areaId, { [field]: value });
      setAreas(prev => prev.map(a => a.id === areaId ? { ...a, [field]: value } : a));
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
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={createForm.is_airport} onChange={e => setCreateForm({...createForm, is_airport: e.target.checked})} className="accent-red-500" /> Airport zone</label>
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
            return (
              <div key={area.id} className="bg-white rounded-2xl border overflow-hidden">
                {/* Area Header — click to expand */}
                <div className="flex items-center gap-4 p-5 cursor-pointer" onClick={() => { setExpandedId(isExpanded ? null : area.id); setEditTab("general"); }}>
                  <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${area.is_active ? 'bg-green-100' : 'bg-gray-100'}`}>
                    <MapPin className={`h-5 w-5 ${area.is_active ? 'text-green-600' : 'text-gray-400'}`} />
                  </div>
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <h4 className="font-bold text-gray-900">{area.name}</h4>
                      {area.is_airport && <span className="px-2 py-0.5 bg-blue-100 text-blue-700 text-xs font-bold rounded-md">AIRPORT</span>}
                      {!area.is_active && <span className="px-2 py-0.5 bg-gray-100 text-gray-500 text-xs font-bold rounded-md">INACTIVE</span>}
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
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                          <FieldInput label="Area Name" value={area.name} onSave={v => handleFieldUpdate(area.id, 'name', v)} />
                          <FieldInput label="City" value={area.city || ''} onSave={v => handleFieldUpdate(area.id, 'city', v)} />
                          <FieldSelect label="Province" value={area.province || 'SK'} options={['SK','AB','MB','ON','BC','QC','NS','NB','PE','NL']} onSave={v => handleFieldUpdate(area.id, 'province', v)} />
                          <FieldInput label="Pickup Radius (km)" value={area.max_pickup_radius_km || 5} type="number" onSave={v => handleFieldUpdate(area.id, 'max_pickup_radius_km', parseFloat(v))} />
                          <FieldToggle label="Active" value={area.is_active} onSave={v => handleFieldUpdate(area.id, 'is_active', v)} />
                          <FieldToggle label="Airport Zone" value={area.is_airport} onSave={v => handleFieldUpdate(area.id, 'is_airport', v)} />
                          <div className="md:col-span-3 flex justify-end">
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
                        <div>
                          <h4 className="font-bold text-gray-800 mb-3">Fees</h4>
                          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
                            <FieldInput label="Platform Fee ($)" value={area.platform_fee || 0} type="number" onSave={v => handleFieldUpdate(area.id, 'platform_fee', parseFloat(v))} />
                            <FieldInput label="City Fee ($)" value={area.city_fee || 0} type="number" onSave={v => handleFieldUpdate(area.id, 'city_fee', parseFloat(v))} />
                            <FieldInput label="Airport Fee ($)" value={area.airport_fee || 0} type="number" onSave={v => handleFieldUpdate(area.id, 'airport_fee', parseFloat(v))} />
                          </div>

                          <h4 className="font-bold text-gray-800 mb-3">Taxes</h4>
                          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
                            <FieldInput label="GST Rate (%)" value={area.gst_rate || 5} type="number" onSave={v => handleFieldUpdate(area.id, 'gst_rate', parseFloat(v))} />
                            <FieldInput label="PST Rate (%)" value={area.pst_rate || 0} type="number" onSave={v => handleFieldUpdate(area.id, 'pst_rate', parseFloat(v))} />
                            <FieldInput label="Insurance Fee (%)" value={area.insurance_fee_percent || 2} type="number" onSave={v => handleFieldUpdate(area.id, 'insurance_fee_percent', parseFloat(v))} />
                          </div>

                          <h4 className="font-bold text-gray-800 mb-3">Cancellation Fees</h4>
                          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                            <FieldInput label="Rider cancel — driver on the way ($)" value={area.rider_cancel_fee_before_driver || 0} type="number" onSave={v => handleFieldUpdate(area.id, 'rider_cancel_fee_before_driver', parseFloat(v))} />
                            <FieldInput label="Rider cancel — driver arrived (total $)" value={area.rider_cancel_fee_after_arrival || 4.50} type="number" onSave={v => handleFieldUpdate(area.id, 'rider_cancel_fee_after_arrival', parseFloat(v))} />
                            <FieldInput label="↳ Driver gets ($)" value={area.cancel_fee_driver_share || 4.00} type="number" onSave={v => handleFieldUpdate(area.id, 'cancel_fee_driver_share', parseFloat(v))} />
                            <FieldInput label="↳ Admin gets ($)" value={area.cancel_fee_admin_share || 0.50} type="number" onSave={v => handleFieldUpdate(area.id, 'cancel_fee_admin_share', parseFloat(v))} />
                            <FieldInput label="Rider cancel — ride started" value="Full fare" type="text" onSave={() => {}} />
                            <FieldInput label="Driver cancel penalty ($)" value={area.driver_cancel_fee || 0} type="number" onSave={v => handleFieldUpdate(area.id, 'driver_cancel_fee', parseFloat(v))} />
                            <FieldInput label="Free cancel window (seconds)" value={area.free_cancel_window_seconds || 120} type="number" onSave={v => handleFieldUpdate(area.id, 'free_cancel_window_seconds', parseInt(v))} />
                          </div>
                        </div>
                      )}

                      {/* Spinr Pass Tab */}
                      {editTab === 'subscriptions' && (
                        <div>
                          <div className="flex items-center justify-between mb-4">
                            <div>
                              <h4 className="font-bold text-gray-800">Spinr Pass Plans</h4>
                              <p className="text-sm text-gray-500">Select which subscription plans are available in this area</p>
                            </div>
                            <FieldToggle label="Enabled" value={area.spinr_pass_enabled !== false} onSave={v => handleFieldUpdate(area.id, 'spinr_pass_enabled', v)} />
                          </div>
                          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                            {plans.map(plan => {
                              const selected = (area.subscription_plan_ids || []).includes(plan.id);
                              return (
                                <label key={plan.id} className={`flex items-center gap-3 p-4 rounded-xl border cursor-pointer transition ${selected ? 'bg-red-50 border-red-200' : 'bg-gray-50 border-gray-100 hover:border-gray-300'}`}>
                                  <input type="checkbox" checked={selected} className="accent-red-500 w-4 h-4" onChange={() => {
                                    const current = area.subscription_plan_ids || [];
                                    const next = selected ? current.filter((id: string) => id !== plan.id) : [...current, plan.id];
                                    handleFieldUpdate(area.id, 'subscription_plan_ids', next);
                                  }} />
                                  <div className="flex-1">
                                    <div className="font-semibold text-gray-800">{plan.name}</div>
                                    <div className="text-sm text-gray-500">${plan.price?.toFixed(2)} · {plan.rides_per_day === -1 ? 'Unlimited' : plan.rides_per_day + ' rides/day'}</div>
                                  </div>
                                  {!plan.is_active && <span className="text-xs text-yellow-600 bg-yellow-50 px-2 py-0.5 rounded">Inactive</span>}
                                </label>
                              );
                            })}
                            {plans.length === 0 && <p className="text-gray-400 text-sm col-span-2">No subscription plans created yet. Go to Spinr Pass to create plans.</p>}
                          </div>
                        </div>
                      )}

                      {/* Documents Tab */}
                      {editTab === 'documents' && (
                        <DocumentsEditor
                          docs={area.required_documents || []}
                          onSave={d => handleFieldUpdate(area.id, 'required_documents', d)}
                        />
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
    { key: 'drivers_license', label: "Driver's License", has_expiry: true, required: true },
    { key: 'vehicle_insurance', label: 'Vehicle Insurance', has_expiry: true, required: true },
    { key: 'vehicle_registration', label: 'Vehicle Registration', has_expiry: true, required: true },
    { key: 'background_check', label: 'Background Check', has_expiry: true, required: true },
    { key: 'vehicle_inspection', label: 'Vehicle Inspection', has_expiry: true, required: true },
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
    setRows([...rows, { key: '', label: '', has_expiry: false, required: true }]);
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
                    <div className="flex items-center gap-4">
                      <label className="flex items-center gap-2 text-sm cursor-pointer">
                        <input type="checkbox" checked={r.has_expiry} onChange={e => update(i, 'has_expiry', e.target.checked)} className="accent-red-500 w-4 h-4" />
                        <span>Requires expiry date</span>
                      </label>
                      <label className="flex items-center gap-2 text-sm cursor-pointer">
                        <input type="checkbox" checked={r.required !== false} onChange={e => update(i, 'required', e.target.checked)} className="accent-red-500 w-4 h-4" />
                        <span>Required</span>
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
