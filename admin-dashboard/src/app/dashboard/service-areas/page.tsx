"use client";

import { useEffect, useState, Suspense } from "react";
import { useRouter } from "next/navigation";
import { useToast } from "@/components/ui/use-toast";
import { PageHeader } from "@/components/page-header";
import { useCrudToast } from "@/components/ui/use-crud-toast";
import {
    AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
    AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { getServiceAreas, createServiceArea, updateServiceArea, deleteServiceArea, getSubscriptionPlans, createSubscriptionPlan, updateSubscriptionPlan, deleteSubscriptionPlan, getDriverSubscriptions, getAreaFees, createAreaFee, updateAreaFee, deleteAreaFee, getVehicleTypes, getIncentives, createIncentive, toggleIncentive, deleteIncentive } from "@/lib/api";
import { Plus, Trash2, Pencil, MapPin, Settings, DollarSign, Car, CreditCard, ChevronDown, ChevronUp, ToggleLeft, ToggleRight, FileText, Plane, Radar, Gift, ArrowRightLeft, Flame } from "lucide-react";
import { useRequireModule } from "@/hooks/useRequireModule";
import { useFeatureFlag } from "@/hooks/useFeatureFlag";
import { Badge } from "@/components/ui/badge";
import { isTaxJustificationValid } from "@/lib/taxJustificationSchema";
import { isSpinrPassPlanNameValid, isSpinrPassPlanPriceValid } from "@/lib/spinrPassAreaPlanSchema";
import { isServiceAreaNameValid, isAirportZoneValid } from "@/lib/serviceAreaFormSchema";
import {
  GeofenceMap, regulatoryDefaultsForProvince, CITY_PRESETS, polygonToText,
  getAreaPolygon, getAreaCenter, FieldInput, FieldTextarea, FieldToggle,
} from "./_components/service-area-shared";
import VehiclePricingEditor from "./_components/vehicle-pricing-editor";
import DocumentsEditor from "./_components/documents-editor";
import CascadeEditor from "./_components/cascade-editor";
import IncentivesTab from "./_components/incentives-tab";
import SurgeHistoryChart from "./_components/surge-history-chart";
import { AreaHeatmapOverrides } from "./_components/area-heatmap-overrides";
import HeatmapConfigTab from "./_components/heatmap-config-tab";
import GeneralTabForm from "./_components/general-tab-form";

export default function ServiceAreasPage() {
  const { allowed } = useRequireModule("service_areas");
  const themeV2Enabled = useFeatureFlag("admin_theme_v2_enabled");
  const router = useRouter();
  const { toast } = useToast();
  const crudToast = useCrudToast();
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(null);
  const [areas, setAreas] = useState<any[]>([]);
  const [plans, setPlans] = useState<any[]>([]);
  // Existing vehicle types (from /api/admin/vehicle-types) so the
  // Vehicle Pricing editor can render a dropdown of known types
  // instead of a free-text input the operator has to spell exactly.
  const [vehicleTypes, setVehicleTypes] = useState<{ id: string; name: string }[]>([]);
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
    regulatory_authority: regulatoryDefaultsForProvince("SK").authority, regulatory_region: regulatoryDefaultsForProvince("SK").region,
    regulatory_requirements_url: "", regulatory_notes: "",
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
      const [a, p, vt] = await Promise.all([
        getServiceAreas(),
        getSubscriptionPlans().catch(() => []),
        getVehicleTypes().catch(() => [] as any[]),
      ]);
      setAreas(Array.isArray(a) ? a : []);
      setPlans(p);
      setVehicleTypes((vt || []).map((v: any) => ({ id: v.id, name: v.name })));
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
        regulatory_authority: regulatoryDefaultsForProvince(p.province).authority,
        regulatory_region: regulatoryDefaultsForProvince(p.province).region,
        polygon: p.polygon, polygonText: polygonToText(p.polygon),
      });
      setMapKey(k => k + 1);
    }
  };

  const handleCreate = async () => {
    if (!isServiceAreaNameValid(createForm.name)) return;
    try {
      await createServiceArea({
        name: createForm.name, city: createForm.city, province: createForm.province,
        regulatory_authority: createForm.regulatory_authority || regulatoryDefaultsForProvince(createForm.province).authority,
        regulatory_region: createForm.regulatory_region || regulatoryDefaultsForProvince(createForm.province).region,
        regulatory_requirements_url: createForm.regulatory_requirements_url || "",
        regulatory_notes: createForm.regulatory_notes || "",
        geojson: { type: "Polygon", coordinates: [createForm.polygon.map(p => [p.lng, p.lat])] },
        is_active: createForm.is_active, is_airport: createForm.is_airport,
        // Defaults
        airport_fee: createForm.is_airport ? 5.0 : 0,
        gst_rate: 5.0, pst_rate: createForm.province === 'SK' ? 6.0 : createForm.province === 'AB' ? 0 : 7.0,
        insurance_fee_percent: 2.0, vehicle_pricing: [], subscription_plan_ids: [],
        spinr_pass_enabled: true, surge_enabled: false, surge_multiplier: 1.0,
        max_pickup_radius_km: 5.0, currency: 'CAD',
      });
      setShowCreate(false);
      setCreateForm({ name: "", city: "", province: "SK", preset: "", regulatory_authority: regulatoryDefaultsForProvince("SK").authority, regulatory_region: regulatoryDefaultsForProvince("SK").region, regulatory_requirements_url: "", regulatory_notes: "", polygon: [], polygonText: "", is_active: true, is_airport: false });
      crudToast.created("Service area");
      load();
    } catch (e) { crudToast.error("create service area", e); }
  };

  const handleCreateAirportSubRegion = async (parentId: string) => {
    const parent = areas.find(a => a.id === parentId);
    if (!isAirportZoneValid(airportForm.name, airportForm.polygon.length)) {
      crudToast.warn("Missing airport boundary", "Please enter a name and draw the airport boundary on the map.");
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
      crudToast.created("Airport zone");
      load();
    } catch (e) { crudToast.error("create airport zone", e); }
  };

  // A29 (ACTION_ITEMS.md): GST/PST/HST config carries real regulatory +
  // financial weight (every rider's charge, CRA/SK remittance), so the
  // backend now requires a written justification for any of these fields —
  // mirroring the existing surge-above-cap justification prompt. Ask for it
  // here, before the request, rather than letting the save silently 400.
  const TAX_FIELDS = new Set(["gst_enabled", "gst_rate", "pst_enabled", "pst_rate", "hst_enabled", "hst_rate"]);

  const handleFieldUpdate = async (areaId: string, field: string, value: any) => {
    try {
      const payload: Record<string, any> = { [field]: value };
      if (TAX_FIELDS.has(field)) {
        const justification = window.prompt("Reason for this tax-configuration change (required):")?.trim();
        if (!isTaxJustificationValid(justification)) return;
        payload.tax_justification = justification;
      }
      await updateServiceArea(areaId, payload);
      setAreas(prev => prev.map(a => {
        if (a.id === areaId) return { ...a, [field]: value };
        if (a.sub_regions?.length) {
          return { ...a, sub_regions: a.sub_regions.map((s: any) => s.id === areaId ? { ...s, [field]: value } : s) };
        }
        return a;
      }));
      crudToast.updated("Service area");
    } catch (e) { crudToast.error("update field", e); }
  };

  const handleVehiclePricingUpdate = async (areaId: string, pricing: any[]) => {
    await handleFieldUpdate(areaId, 'vehicle_pricing', pricing);
  };

  const handleDelete = (id: string, name: string) => {
    setDeleteTarget({ id, name });
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    try {
      await deleteServiceArea(deleteTarget.id);
      crudToast.deleted("Service area", `"${deleteTarget.name}" removed.`);
      load();
    }
    catch (e) { crudToast.error("delete service area", e); }
    finally { setDeleteTarget(null); }
  };

  if (!allowed) return null;

  return (
    <div>
      <PageHeader
        className="flex items-center justify-between mb-8"
        title="Service Areas"
        description="Configure pricing, fees, taxes & subscriptions per area"
        actions={
          <button onClick={() => setShowCreate(true)} className="flex items-center gap-2 bg-primary text-primary-foreground px-5 py-2.5 rounded-xl font-semibold hover:bg-primary/90">
            <Plus className="h-5 w-5" /> New Area
          </button>
        }
      />

      {/* Create Form */}
      {showCreate && (
        <div className="bg-card rounded-2xl border p-6 mb-6 shadow-sm">
          <h3 className="text-lg font-bold mb-4">Create Service Area</h3>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
            <div>
              <label className="block text-sm font-medium text-muted-foreground mb-1">City Preset</label>
              <select className="w-full border rounded-xl px-4 py-2.5 text-sm" value={createForm.preset} onChange={e => handlePreset(e.target.value)}>
                <option value="">Select city...</option>
                {Object.entries(CITY_PRESETS).map(([k, v]) => <option key={k} value={k}>{v.city}, {v.province}</option>)}
                <option value="custom">Custom</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-muted-foreground mb-1">Area Name *</label>
              <input className="w-full border rounded-xl px-4 py-2.5 text-sm" value={createForm.name} onChange={e => setCreateForm({...createForm, name: e.target.value})} placeholder="e.g. Saskatoon Metro" />
            </div>
            <div>
              <label className="block text-sm font-medium text-muted-foreground mb-1">Province</label>
              <select className="w-full border rounded-xl px-4 py-2.5 text-sm" value={createForm.province} onChange={e => { const province = e.target.value; const defaults = regulatoryDefaultsForProvince(province); setCreateForm({...createForm, province, regulatory_authority: defaults.authority, regulatory_region: defaults.region}); }}>
                {['SK','AB','MB','ON','BC','QC','NS','NB','PE','NL','NT','YT','NU'].map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
          </div>
          <div className="flex items-center gap-4 mb-4">
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={createForm.is_active} onChange={e => setCreateForm({...createForm, is_active: e.target.checked})} className="accent-primary" /> Active</label>
          </div>

          {/* Geofence Map — always visible, draw polygon or select preset */}
          <div className="mb-4">
            <label className="block text-sm font-medium text-muted-foreground mb-2">
              Service Area Boundary {createForm.polygon.length === 0 && <span className="text-destructive">(select a preset or draw on the map)</span>}
            </label>
            <div className="h-64 rounded-xl overflow-hidden border">
              <Suspense fallback={<div className="h-full bg-muted flex items-center justify-center text-muted-foreground">Loading map...</div>}>
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
              <p className="text-xs text-success mt-1">{createForm.polygon.length} points defined</p>
            )}
          </div>

          <div className="flex gap-3">
            <button onClick={handleCreate} className="bg-primary text-primary-foreground px-6 py-2.5 rounded-xl font-semibold hover:bg-primary/90">Create</button>
            <button onClick={() => setShowCreate(false)} className="bg-muted text-foreground px-6 py-2.5 rounded-xl font-semibold">Cancel</button>
          </div>
        </div>
      )}

      {/* Areas List */}
      {loading ? (
        <div className="text-center py-12 text-muted-foreground">Loading...</div>
      ) : areas.length === 0 ? (
        <div className="text-center py-16 bg-card rounded-2xl border">
          <MapPin className="h-12 w-12 text-muted-foreground mx-auto mb-4" />
          <h3 className="text-lg font-bold text-foreground">No service areas</h3>
          <p className="text-muted-foreground">Create your first service area to start operations</p>
        </div>
      ) : (
        <div className="space-y-3">
          {areas.map(area => {
            const isExpanded = expandedId === area.id;
            const subRegions: any[] = area.sub_regions || [];
            return (
              <div key={area.id} className="bg-card rounded-2xl border overflow-hidden">
                {/* Area Header — click to expand */}
                <div className="flex items-center gap-4 p-5 cursor-pointer" role="button" tabIndex={0} aria-expanded={isExpanded} onClick={() => { const newId = isExpanded ? null : area.id; setExpandedId(newId); setEditTab("general"); if (newId && !areaFees[newId]) loadAreaFees(newId); }} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); const newId = isExpanded ? null : area.id; setExpandedId(newId); setEditTab("general"); if (newId && !areaFees[newId]) loadAreaFees(newId); } }}>
                  <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${area.is_active ? 'bg-success/15' : 'bg-muted'}`}>
                    <MapPin className={`h-5 w-5 ${area.is_active ? 'text-success' : 'text-muted-foreground'}`} />
                  </div>
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <h4 className="font-bold text-foreground">{area.name}</h4>
                      {area.is_airport && (
                        themeV2Enabled ? (
                          <Badge variant="outline" className="text-xs">AIRPORT</Badge>
                        ) : (
                          // eslint-disable-next-line no-restricted-syntax -- decorative airport-category badge, not a health-state signal (#2816)
                          <span className="px-2 py-0.5 bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400 text-xs font-bold rounded-md">AIRPORT</span>
                        )
                      )}
                      {!area.is_active && <span className="px-2 py-0.5 bg-muted text-muted-foreground text-xs font-bold rounded-md">INACTIVE</span>}
                      {subRegions.length > 0 && (
                        themeV2Enabled ? (
                          <Badge variant="outline" className="text-xs">{subRegions.length} airport zone{subRegions.length > 1 ? 's' : ''}</Badge>
                        ) : (
                          // eslint-disable-next-line no-restricted-syntax -- decorative airport-zone-count badge, not a health-state signal (#2816)
                          <span className="px-2 py-0.5 bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-400 text-xs font-bold rounded-md">{subRegions.length} airport zone{subRegions.length > 1 ? 's' : ''}</span>
                        )
                      )}
                    </div>
                    <p className="text-sm text-muted-foreground">{area.city || ''}{area.province ? `, ${area.province}` : ''} · GST {area.gst_rate || 5}% · PST {area.pst_rate || 0}%</p>
                  </div>
                  <div className="text-sm text-muted-foreground">{area.vehicle_pricing?.length || 0} vehicles · {area.subscription_plan_ids?.length || 0} plans</div>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      router.push(`/dashboard/monitoring?areaId=${encodeURIComponent(area.id)}`);
                    }}
                    // eslint-disable-next-line no-restricted-syntax -- decorative brand accent for the live-monitor link button, not a health-state signal (#2816)
                    className="flex items-center gap-1.5 rounded-lg border border-violet-200 bg-violet-50 dark:border-violet-800 dark:bg-violet-900/20 px-3 py-1.5 text-xs font-semibold text-violet-700 dark:text-violet-400 transition-colors hover:bg-violet-100"
                    title="Open this area on the live monitor"
                  >
                    <Radar className="h-3.5 w-3.5" />
                    View on live monitor
                  </button>
                  {isExpanded ? <ChevronUp className="h-5 w-5 text-muted-foreground" /> : <ChevronDown className="h-5 w-5 text-muted-foreground" />}
                </div>

                {/* Expanded Config */}
                {isExpanded && (
                  <div className="border-t">
                    {/* Tabs */}
                    <div className="flex gap-1 px-5 pt-3 bg-muted overflow-x-auto">
                      {[
                        { key: 'general', label: 'General', icon: Settings },
                        { key: 'pricing', label: 'Vehicle Pricing', icon: Car },
                        { key: 'fees', label: 'Fees & Taxes', icon: DollarSign },
                        { key: 'subscriptions', label: 'Spinr Pass', icon: CreditCard },
                        { key: 'documents', label: 'Documents', icon: FileText },
                        { key: 'incentives', label: 'Incentives', icon: Gift },
                        { key: 'subregions', label: 'Airport Zones', icon: Plane },
                        { key: 'cascade', label: 'Dispatch Cascade', icon: ArrowRightLeft },
                        // Labelled "(All Areas)" at the tab itself, not only inside the
                        // panel: the scope warning has to reach the operator before they
                        // decide this is a per-area screen.
                        { key: 'heatmap', label: 'Driver Heatmap (All Areas)', icon: Flame },
                      ].map(tab => (
                        <button key={tab.key} onClick={() => setEditTab(tab.key)}
                          className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-semibold rounded-t-lg transition ${editTab === tab.key ? 'bg-card text-primary border-t-2 border-primary' : 'text-muted-foreground hover:text-foreground'}`}>
                          <tab.icon className="h-4 w-4" /> {tab.label}
                        </button>
                      ))}
                    </div>

                    <div className="p-5">
                      {/* General Tab */}
                      {editTab === 'general' && (
                        <>
                          <GeneralTabForm area={area} onSave={async (updates) => {
                            try {
                              await updateServiceArea(area.id, updates);
                              setAreas(prev => prev.map(a => a.id === area.id ? { ...a, ...updates } : a));
                              crudToast.updated("Service area");
                            } catch (e) { crudToast.error("save service area", e); }
                          }} onDelete={() => handleDelete(area.id, area.name)} />
                          <SurgeHistoryChart areaId={area.id} areaName={area.name} />
                        </>
                      )}

                      {/* Vehicle Pricing Tab */}
                      {editTab === 'pricing' && (
                        <VehiclePricingEditor pricing={area.vehicle_pricing || []} vehicleTypes={vehicleTypes} onSave={p => handleVehiclePricingUpdate(area.id, p)} />
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
                          onRequiredToggle={v => handleFieldUpdate(area.id, 'subscription_required', v)}
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
                      )}

                      {/* Incentives Tab */}
                      {editTab === 'incentives' && (
                        <IncentivesTab areaId={area.id} areaName={area.name} vehicleTypes={vehicleTypes} />
                      )}

                      {/* Dispatch Cascade Tab */}
                      {editTab === 'cascade' && (
                        <CascadeEditor
                          cascadeMap={area.vehicle_cascade_map || []}
                          vehicleTypes={vehicleTypes.filter(vt =>
                            (area.vehicle_pricing || []).some((p: any) => p.vehicle_type === vt.name)
                          )}
                          onSave={async (map) => {
                            try {
                              await updateServiceArea(area.id, { vehicle_cascade_map: map });
                              setAreas(prev => prev.map(a => a.id === area.id ? { ...a, vehicle_cascade_map: map } : a));
                              crudToast.updated("Dispatch cascade");
                            } catch (e) { crudToast.error("save cascade", e); }
                          }}
                        />
                      )}

                      {/* Heatmap Config Tab (global app_settings, not per-area) */}
                      {editTab === 'heatmap' && (
                        <>
                          <AreaHeatmapOverrides
                            areaId={area.id}
                            areaName={area.name}
                            onSuccess={() => crudToast.updated(`${area.name} heatmap tuning`)}
                            onError={(e) => crudToast.error("save area heatmap tuning", e)}
                          />
                          <HeatmapConfigTab onSuccess={() => crudToast.updated("Heatmap config")} onError={(e) => crudToast.error("save heatmap config", e)} />
                        </>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete "{deleteTarget?.name}"?</AlertDialogTitle>
            <AlertDialogDescription>This cannot be undone.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={confirmDelete} className="bg-destructive hover:bg-destructive/90">Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

// ─── Area Fees Editor ───

function AreaFeesEditor({ areaId, area, fees, loading, onReload, onFieldUpdate }: {
    areaId: string; area: any; fees: any[]; loading: boolean;
    onReload: () => void; onFieldUpdate: (areaId: string, field: string, value: any) => void;
}) {
    const { toast } = useToast();
    const crudToast = useCrudToast();
    const [feeDeleteTarget, setFeeDeleteTarget] = useState<string | null>(null);
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
            crudToast.created("Fee");
            onReload();
        } catch (e) { crudToast.error("create fee", e); }
        setSaving(false);
    };

    const handleUpdate = async (feeId: string, data: any) => {
        try {
            await updateAreaFee(areaId, feeId, data);
            crudToast.updated("Fee");
            onReload();
        } catch (e) { crudToast.error("update fee", e); }
    };

    const handleDelete = (feeId: string) => {
        setFeeDeleteTarget(feeId);
    };

    const confirmFeeDelete = async () => {
        if (!feeDeleteTarget) return;
        try {
            await deleteAreaFee(areaId, feeDeleteTarget);
            crudToast.deleted("Fee");
            onReload();
        }
        catch (e) { crudToast.error("delete fee", e); }
        finally { setFeeDeleteTarget(null); }
    };

    return (
        <>
        <div className="space-y-6">
            {/* SECTION 1: Area Fees */}
            <div>
                <div className="flex items-center justify-between mb-4">
                    <div>
                        <h4 className="font-bold text-foreground">Area Fees</h4>
                        <p className="text-sm text-muted-foreground">Fees applied to rides in this area. Calculated dynamically based on type.</p>
                    </div>
                    <button onClick={handleCreate} disabled={saving}
                        className="flex items-center gap-1.5 bg-primary text-primary-foreground px-4 py-2 rounded-xl text-sm font-semibold hover:bg-primary/90 disabled:opacity-50">
                        <Plus className="h-4 w-4" /> Add Fee
                    </button>
                </div>

                {loading ? (
                    <div className="text-center py-8 text-muted-foreground">Loading fees...</div>
                ) : fees.length === 0 ? (
                    <div className="text-center py-8 bg-muted rounded-xl border-2 border-dashed">
                        <DollarSign className="h-8 w-8 text-muted-foreground mx-auto mb-2" />
                        <p className="text-sm text-muted-foreground">No fees configured</p>
                        <p className="text-xs text-muted-foreground">Add fees like airport surcharge, night fee, city fee, etc.</p>
                    </div>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                        {fees.map(fee => (
                            <div key={fee.id} className={`rounded-xl border p-4 ${fee.is_active ? 'bg-card' : 'bg-muted opacity-60'}`}>
                                <div className="flex items-start justify-between mb-3">
                                    <div>
                                        <p className="font-bold text-foreground">{fee.fee_name || fee.fee_type || 'Fee'}</p>
                                        <p className="text-xs text-muted-foreground mt-0.5">{fee.fee_type} · {fee.calc_mode || 'flat'}</p>
                                    </div>
                                    <div className="text-right">
                                        <p className="text-lg font-bold text-foreground">
                                            {fee.calc_mode === 'percentage' ? `${fee.amount}%` : `$${Number(fee.amount || 0).toFixed(2)}`}
                                            {fee.calc_mode === 'per_km' && '/km'}
                                        </p>
                                    </div>
                                </div>
                                {fee.description && <p className="text-xs text-muted-foreground mb-3">{fee.description}</p>}
                                {fee.conditions && fee.conditions.start_hour !== undefined && (
                                    <p className="text-xs text-muted-foreground mb-3">Hours: {fee.conditions.start_hour}:00 - {fee.conditions.end_hour}:00</p>
                                )}
                                <div className="flex items-center gap-2 pt-2 border-t">
                                    <button onClick={() => setEditingFee(editingFee?.id === fee.id ? null : fee)} className="text-xs text-muted-foreground hover:text-primary font-medium flex items-center gap-1">
                                        <Pencil className="h-3 w-3" /> Edit
                                    </button>
                                    <button onClick={() => handleUpdate(fee.id, { is_active: !fee.is_active })} className="text-xs text-muted-foreground hover:text-foreground font-medium ml-auto">
                                        {fee.is_active ? 'Disable' : 'Enable'}
                                    </button>
                                    <button onClick={() => handleDelete(fee.id)} className="text-xs text-muted-foreground hover:text-destructive font-medium">
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
                <h4 className="font-bold text-foreground mb-3">Tax Configuration</h4>
                <div className="bg-card rounded-xl border p-4">
                    <div className="flex items-center gap-4 mb-4">
                        <label className="flex items-center gap-2 text-sm">
                            <input type="radio" name={`tax-${areaId}`} checked={!area.hst_enabled}
                                onChange={() => onFieldUpdate(areaId, 'hst_enabled', false)} className="accent-primary" />
                            GST + PST (separate)
                        </label>
                        <label className="flex items-center gap-2 text-sm">
                            <input type="radio" name={`tax-${areaId}`} checked={!!area.hst_enabled}
                                onChange={() => onFieldUpdate(areaId, 'hst_enabled', true)} className="accent-primary" />
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
                <h4 className="font-bold text-foreground mb-3">Cancellation Fees</h4>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <FieldInput label="Rider cancel — driver on the way ($)" value={area.rider_cancel_fee_before_driver || 0} type="number" onSave={v => onFieldUpdate(areaId, 'rider_cancel_fee_before_driver', parseFloat(v))} />
                    <FieldInput label="Rider cancel — driver arrived ($)" value={area.rider_cancel_fee_after_arrival || 4.50} type="number" onSave={v => onFieldUpdate(areaId, 'rider_cancel_fee_after_arrival', parseFloat(v))} />
                    <FieldInput label="Driver gets ($)" value={area.cancel_fee_driver_share || 4.00} type="number" onSave={v => onFieldUpdate(areaId, 'cancel_fee_driver_share', parseFloat(v))} />
                    <FieldInput label="Admin gets ($)" value={area.cancel_fee_admin_share || 0.50} type="number" onSave={v => onFieldUpdate(areaId, 'cancel_fee_admin_share', parseFloat(v))} />
                    <FieldInput label="Driver cancel penalty ($)" value={area.driver_cancel_fee || 0} type="number" onSave={v => onFieldUpdate(areaId, 'driver_cancel_fee', parseFloat(v))} />
                    <FieldInput label="Free cancel window (sec)" value={area.free_cancel_window_seconds || 120} type="number" onSave={v => onFieldUpdate(areaId, 'free_cancel_window_seconds', parseInt(v))} />
                </div>
            </div>

            {/* SECTION 4: Referral Rewards */}
            <div>
                <h4 className="font-bold text-foreground mb-3">Referral Rewards</h4>
                <p className="text-xs text-muted-foreground mb-3">
                    Per-area referral rewards (CAD). Riders/drivers whose area resolves here see and earn these amounts; users not mapped to any area fall back to the global default. Set any reward to $0 to turn that side off (e.g. driver referee reward defaults to $0 — set it above 0 to pay a signup bonus to the referred driver). &quot;Rides within (days)&quot; is the deadline to complete the required rides before the referral expires unpaid — set 0 for no deadline.
                </p>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <FieldInput label="Rider — referrer reward ($)" value={area.rider_referrer_reward ?? 5} type="number" onSave={v => onFieldUpdate(areaId, 'rider_referrer_reward', parseFloat(v))} />
                    <FieldInput label="Rider — referee reward ($)" value={area.rider_referee_reward ?? 5} type="number" onSave={v => onFieldUpdate(areaId, 'rider_referee_reward', parseFloat(v))} />
                    <FieldInput label="Rider — rides required" value={area.rider_referral_rides_required ?? 1} type="number" onSave={v => onFieldUpdate(areaId, 'rider_referral_rides_required', parseInt(v))} />
                    <FieldInput label="Rider — rides within (days)" value={area.rider_referral_window_days ?? 30} type="number" onSave={v => onFieldUpdate(areaId, 'rider_referral_window_days', parseInt(v))} />
                    <FieldInput label="Driver — referrer reward ($)" value={area.driver_referral_reward ?? 10} type="number" onSave={v => onFieldUpdate(areaId, 'driver_referral_reward', parseFloat(v))} />
                    <FieldInput label="Driver — referee reward ($)" value={area.driver_referee_reward ?? 0} type="number" onSave={v => onFieldUpdate(areaId, 'driver_referee_reward', parseFloat(v))} />
                    <FieldInput label="Driver — rides required" value={area.driver_referral_rides_required ?? 10} type="number" onSave={v => onFieldUpdate(areaId, 'driver_referral_rides_required', parseInt(v))} />
                    <FieldInput label="Driver — rides within (days)" value={area.driver_referral_window_days ?? 30} type="number" onSave={v => onFieldUpdate(areaId, 'driver_referral_window_days', parseInt(v))} />
                </div>
                <p className="text-xs text-muted-foreground mt-4 mb-2">
                    Referral Terms &amp; Conditions shown on the rider/driver &quot;Refer &amp; Earn&quot; screens. Leave blank to auto-generate the default sentence from the reward amounts above.
                </p>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <FieldTextarea label="Rider — referral T&C" value={area.rider_referral_terms ?? ''} placeholder="e.g. Give $5, get $5 when your friend takes their first ride. Rewards credited to your wallet…" onSave={v => onFieldUpdate(areaId, 'rider_referral_terms', v)} />
                    <FieldTextarea label="Driver — referral T&C" value={area.driver_referral_terms ?? ''} placeholder="e.g. Earn $10 for each driver who signs up with your code and completes 10 rides…" onSave={v => onFieldUpdate(areaId, 'driver_referral_terms', v)} />
                </div>
            </div>
        </div>

        <AlertDialog open={!!feeDeleteTarget} onOpenChange={(open) => { if (!open) setFeeDeleteTarget(null); }}>
            <AlertDialogContent>
                <AlertDialogHeader>
                    <AlertDialogTitle>Delete fee?</AlertDialogTitle>
                    <AlertDialogDescription>This cannot be undone.</AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                    <AlertDialogAction onClick={confirmFeeDelete} className="bg-destructive hover:bg-destructive/90">Delete</AlertDialogAction>
                </AlertDialogFooter>
            </AlertDialogContent>
        </AlertDialog>
        </>
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
                    <label className="block text-xs font-semibold text-muted-foreground mb-1">Fee Name</label>
                    <input className="w-full border rounded-lg px-3 py-2 text-sm" value={form.fee_name}
                        onChange={e => setForm({ ...form, fee_name: e.target.value })} />
                </div>
                <div>
                    <label className="block text-xs font-semibold text-muted-foreground mb-1">Fee Type</label>
                    <select className="w-full border rounded-lg px-3 py-2 text-sm" value={form.fee_type}
                        onChange={e => setForm({ ...form, fee_type: e.target.value })}>
                        {feeTypes.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                    </select>
                </div>
                <div>
                    <label className="block text-xs font-semibold text-muted-foreground mb-1">Calculation Mode</label>
                    <select className="w-full border rounded-lg px-3 py-2 text-sm" value={form.calc_mode}
                        onChange={e => setForm({ ...form, calc_mode: e.target.value })}>
                        {calcModes.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
                    </select>
                </div>
                <div>
                    <label className="block text-xs font-semibold text-muted-foreground mb-1">
                        Amount {form.calc_mode === 'percentage' ? '(%)' : form.calc_mode === 'per_km' ? '($/km)' : '($)'}
                    </label>
                    <input className="w-full border rounded-lg px-3 py-2 text-sm" type="number" step="0.01"
                        value={form.amount} onChange={e => setForm({ ...form, amount: parseFloat(e.target.value) || 0 })} />
                </div>
            </div>
            <div>
                <label className="block text-xs font-semibold text-muted-foreground mb-1">Description (optional)</label>
                <input className="w-full border rounded-lg px-3 py-2 text-sm" value={form.description}
                    onChange={e => setForm({ ...form, description: e.target.value })} />
            </div>
            {form.fee_type === 'night' && (
                <div className="grid grid-cols-2 gap-3">
                    <div>
                        <label className="block text-xs font-semibold text-muted-foreground mb-1">Start Hour (0-23)</label>
                        <input className="w-full border rounded-lg px-3 py-2 text-sm" type="number" min="0" max="23"
                            value={form.conditions.start_hour ?? 23}
                            onChange={e => setForm({ ...form, conditions: { ...form.conditions, start_hour: parseInt(e.target.value) } })} />
                    </div>
                    <div>
                        <label className="block text-xs font-semibold text-muted-foreground mb-1">End Hour (0-23)</label>
                        <input className="w-full border rounded-lg px-3 py-2 text-sm" type="number" min="0" max="23"
                            value={form.conditions.end_hour ?? 5}
                            onChange={e => setForm({ ...form, conditions: { ...form.conditions, end_hour: parseInt(e.target.value) } })} />
                    </div>
                </div>
            )}
            <div className="flex gap-2 pt-1">
                <button onClick={async () => { setSaving(true); await onSave(form); setSaving(false); }} disabled={saving}
                    className="bg-primary text-primary-foreground px-4 py-1.5 rounded-lg text-sm font-semibold hover:bg-primary/90 disabled:opacity-50">
                    {saving ? 'Saving...' : 'Save'}
                </button>
                <button onClick={onCancel} className="bg-muted text-foreground px-4 py-1.5 rounded-lg text-sm font-semibold">Cancel</button>
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

function SpinrPassAreaTab({ area, plans, onToggle, onRequiredToggle, onPlansChanged }: {
  area: any; plans: any[]; onToggle: (v: boolean) => void; onRequiredToggle: (v: boolean) => void; onPlansChanged: () => void;
}) {
  const { toast } = useToast();
  const [planDeleteTarget, setPlanDeleteTarget] = useState<{ id: string; name: string } | null>(null);
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
    if (!isSpinrPassPlanNameValid(form.name) || !isSpinrPassPlanPriceValid(form.price)) return;
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
    } catch (e: any) { toast({ title: "Failed to save plan", description: e?.message, variant: "destructive" }); }
  };

  const handleEdit = (p: any) => {
    setEditingId(p.id);
    setForm({ name: p.name, price: String(p.price), duration_days: p.duration_days, rides_per_day: p.rides_per_day, description: p.description || "", features: (p.features || []).join(", "), is_active: p.is_active });
    setShowForm(true);
  };

  const handleDeletePlan = (p: any) => {
    setPlanDeleteTarget({ id: p.id, name: p.name });
  };

  const confirmPlanDelete = async () => {
    if (!planDeleteTarget) return;
    try { await deleteSubscriptionPlan(planDeleteTarget.id); onPlansChanged(); }
    catch (e: any) { toast({ title: "Failed to delete plan", description: e?.message, variant: "destructive" }); }
    finally { setPlanDeleteTarget(null); }
  };

  const handleTogglePlan = async (p: any) => {
    await updateSubscriptionPlan(p.id, { is_active: !p.is_active }); onPlansChanged();
  };

  const getDurationLabel = (days: number) => DURATION_OPTIONS.find(d => d.value === days)?.label || `${days} days`;

  const enabled = area.spinr_pass_enabled !== false;
  const required = area.subscription_required === true;

  return (
    <div>
      {/* Kill switch */}
      <div className={`flex items-center justify-between p-4 rounded-xl mb-3 ${enabled ? 'bg-success/10 border border-success/30' : 'bg-muted border border-border'}`}>
        <div>
          <h4 className="font-bold text-foreground">Spinr Pass for {area.name}</h4>
          <p className="text-sm text-muted-foreground">
            {enabled ? 'Drivers in this area can see and subscribe to plans' : 'Disabled — drivers see "It\'s Free Right Now!" instead'}
          </p>
        </div>
        <FieldToggle label={enabled ? "ON" : "OFF"} value={enabled} onSave={onToggle} />
      </div>

      {/* Mandatory-subscription toggle */}
      <div className={`flex items-center justify-between p-4 rounded-xl mb-5 ${required ? 'bg-warning/10 border border-warning/30' : 'bg-muted border border-border'}`}>
        <div>
          <h4 className="font-bold text-foreground">Require Subscription to Drive</h4>
          <p className="text-sm text-muted-foreground">
            {required
              ? 'Drivers must have an active Spinr Pass to go online, receive offers, and accept rides'
              : 'Spinr Pass is optional — all verified drivers can work in this area'}
          </p>
        </div>
        <FieldToggle label={required ? "Required" : "Optional"} value={required} onSave={onRequiredToggle} />
      </div>

      {/* Plan management */}
      <div className="flex items-center justify-between mb-4">
        <h4 className="font-bold text-foreground">Subscription Plans</h4>
        <button onClick={() => { resetForm(); setShowForm(true); }} className="flex items-center gap-1.5 bg-primary text-primary-foreground px-4 py-2 rounded-xl text-sm font-semibold hover:bg-primary/90">
          <Plus className="h-4 w-4" /> New Plan
        </button>
      </div>

      {/* Create/Edit Form */}
      {showForm && (
        <div className="bg-card rounded-xl border p-5 mb-5 shadow-sm">
          <h5 className="font-bold mb-3">{editingId ? "Edit Plan" : "New Plan"}</h5>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">
            <div>
              <label className="block text-xs font-semibold text-muted-foreground mb-1">Plan Name *</label>
              <input className="w-full border rounded-lg px-3 py-2 text-sm" placeholder="e.g. Basic" value={form.name} onChange={e => setForm({...form, name: e.target.value})} />
            </div>
            <div>
              <label className="block text-xs font-semibold text-muted-foreground mb-1">Price (CAD) *</label>
              <input className="w-full border rounded-lg px-3 py-2 text-sm" type="number" step="0.01" placeholder="19.99" value={form.price} onChange={e => setForm({...form, price: e.target.value})} />
            </div>
            <div>
              <label className="block text-xs font-semibold text-muted-foreground mb-1">Duration</label>
              <select className="w-full border rounded-lg px-3 py-2 text-sm" value={form.duration_days} onChange={e => setForm({...form, duration_days: parseInt(e.target.value)})}>
                {DURATION_OPTIONS.map(d => <option key={d.value} value={d.value}>{d.label}</option>)}
              </select>
            </div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
            <div>
              <label className="block text-xs font-semibold text-muted-foreground mb-1">Rides Per Day</label>
              <div className="flex gap-1.5 flex-wrap">
                <button onClick={() => setForm({...form, rides_per_day: -1})} className={`px-3 py-1.5 rounded-lg text-xs font-semibold border ${form.rides_per_day === -1 ? "bg-primary text-primary-foreground border-primary" : "bg-card border-border"}`}>Unlimited</button>
                {[4, 8, 12, 20].map(n => (
                  <button key={n} onClick={() => setForm({...form, rides_per_day: n})} className={`px-3 py-1.5 rounded-lg text-xs font-semibold border ${form.rides_per_day === n ? "bg-primary text-primary-foreground border-primary" : "bg-card border-border"}`}>{n}</button>
                ))}
              </div>
            </div>
            <div>
              <label className="block text-xs font-semibold text-muted-foreground mb-1">Description</label>
              <input className="w-full border rounded-lg px-3 py-2 text-sm" placeholder="Brief description" value={form.description} onChange={e => setForm({...form, description: e.target.value})} />
            </div>
          </div>
          <div className="mb-3">
            <label className="block text-xs font-semibold text-muted-foreground mb-1">Features (comma-separated)</label>
            <input className="w-full border rounded-lg px-3 py-2 text-sm" placeholder="Priority support, Surge protection" value={form.features} onChange={e => setForm({...form, features: e.target.value})} />
          </div>
          <div className="flex items-center gap-4 mb-4">
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.is_active} onChange={e => setForm({...form, is_active: e.target.checked})} className="accent-primary" /> Active</label>
          </div>
          <div className="flex gap-2">
            <button onClick={handleSubmit} className="bg-primary text-primary-foreground px-5 py-2 rounded-xl text-sm font-semibold hover:bg-primary/90">{editingId ? "Save" : "Create Plan"}</button>
            <button onClick={resetForm} className="bg-muted text-foreground px-5 py-2 rounded-xl text-sm font-semibold">Cancel</button>
          </div>
        </div>
      )}

      {/* Plans List */}
      {plans.length === 0 ? (
        <div className="text-center py-12 bg-muted rounded-xl">
          <CreditCard className="h-10 w-10 text-muted-foreground mx-auto mb-3" />
          <p className="text-muted-foreground font-medium">No subscription plans yet</p>
          <p className="text-muted-foreground text-sm mt-1">Create your first Spinr Pass plan above</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 mb-6">
          {plans.map(p => (
            <div key={p.id} className={`bg-card rounded-xl border p-5 relative ${!p.is_active ? "opacity-50" : ""}`}>
              <button onClick={() => handleTogglePlan(p)} className="absolute top-3 right-3">
                {p.is_active ? <ToggleRight className="h-5 w-5 text-success" /> : <ToggleLeft className="h-5 w-5 text-muted-foreground" />}
              </button>
              <h5 className="font-bold text-foreground text-lg">{p.name}</h5>
              {p.description && <p className="text-muted-foreground text-xs mt-0.5">{p.description}</p>}
              <div className="mt-2 mb-3">
                <span className="text-2xl font-extrabold text-primary">${p.price?.toFixed(2)}</span>
                <span className="text-muted-foreground text-xs ml-1">/ {getDurationLabel(p.duration_days).toLowerCase()}</span>
              </div>
              <p className="text-sm text-foreground mb-1">
                {p.rides_per_day === -1 ? 'Unlimited rides/day' : `${p.rides_per_day} rides/day`}
              </p>
              <p className="text-xs text-muted-foreground">{p.subscriber_count || 0} subscribers</p>
              {(p.features || []).length > 0 && (
                <div className="border-t mt-3 pt-2">
                  {p.features.map((f: string, i: number) => <p key={i} className="text-xs text-muted-foreground py-0.5">✓ {f}</p>)}
                </div>
              )}
              <div className="flex gap-2 mt-3 pt-2 border-t">
                <button onClick={() => handleEdit(p)} className="flex-1 text-center py-1.5 text-xs font-semibold text-foreground hover:bg-muted rounded-lg"><Pencil className="h-3 w-3 inline mr-1" />Edit</button>
                <button onClick={() => handleDeletePlan(p)} className="flex-1 text-center py-1.5 text-xs font-semibold text-destructive hover:bg-destructive/10 rounded-lg"><Trash2 className="h-3 w-3 inline mr-1" />Delete</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Subscribers */}
      {plans.length > 0 && (
        <div>
          <div className="flex items-center justify-between mb-3">
            <h4 className="font-bold text-foreground">Subscribers</h4>
            {!subsLoaded && <button onClick={loadSubs} className="text-sm text-primary font-semibold hover:underline">Load subscribers</button>}
          </div>
          {subsLoaded && (
            subs.length === 0 ? (
              <p className="text-muted-foreground text-sm">No subscribers yet.</p>
            ) : (
              <div className="bg-card rounded-xl border overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-muted text-left">
                    <tr>
                      <th className="px-4 py-2 font-semibold text-foreground text-xs">Driver</th>
                      <th className="px-4 py-2 font-semibold text-foreground text-xs">Plan</th>
                      <th className="px-4 py-2 font-semibold text-foreground text-xs">Status</th>
                      <th className="px-4 py-2 font-semibold text-foreground text-xs">Expires</th>
                    </tr>
                  </thead>
                  <tbody>
                    {subs.map(s => (
                      <tr key={s.id} className="border-t">
                        <td className="px-4 py-2 font-mono text-xs">{s.driver_id?.slice(0, 8)}...</td>
                        <td className="px-4 py-2">{s.plan_name}</td>
                        <td className="px-4 py-2">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${s.status === 'active' ? 'bg-success/15 text-success' : 'bg-muted text-muted-foreground'}`}>{s.status?.toUpperCase()}</span>
                        </td>
                        <td className="px-4 py-2 text-xs text-muted-foreground">{s.expires_at ? new Date(s.expires_at).toLocaleDateString() : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          )}
        </div>
      )}

      <AlertDialog open={!!planDeleteTarget} onOpenChange={(open) => { if (!open) setPlanDeleteTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete "{planDeleteTarget?.name}" plan?</AlertDialogTitle>
            <AlertDialogDescription>This cannot be undone.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={confirmPlanDelete} className="bg-destructive hover:bg-destructive/90">Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

