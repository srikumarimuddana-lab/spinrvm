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
import { getServiceAreas, createServiceArea, updateServiceArea, deleteServiceArea, getSubscriptionPlans, getAreaFees, getVehicleTypes } from "@/lib/api";
import { Plus, Trash2, Pencil, MapPin, Settings, DollarSign, Car, CreditCard, ChevronDown, ChevronUp, ToggleLeft, ToggleRight, FileText, Plane, Radar, Gift, ArrowRightLeft, Flame } from "lucide-react";
import { useRequireModule } from "@/hooks/useRequireModule";
import { useFeatureFlag } from "@/hooks/useFeatureFlag";
import { Badge } from "@/components/ui/badge";
import { isTaxJustificationValid } from "@/lib/taxJustificationSchema";
import { isServiceAreaNameValid, isAirportZoneValid } from "@/lib/serviceAreaFormSchema";
import {
  GeofenceMap, regulatoryDefaultsForProvince, CITY_PRESETS, polygonToText,
} from "./_components/service-area-shared";
import VehiclePricingEditor from "./_components/vehicle-pricing-editor";
import DocumentsEditor from "./_components/documents-editor";
import CascadeEditor from "./_components/cascade-editor";
import IncentivesTab from "./_components/incentives-tab";
import SurgeHistoryChart from "./_components/surge-history-chart";
import { AreaHeatmapOverrides } from "./_components/area-heatmap-overrides";
import HeatmapConfigTab from "./_components/heatmap-config-tab";
import GeneralTabForm from "./_components/general-tab-form";
import AreaFeesEditor from "./_components/area-fees-editor";
import SpinrPassAreaTab from "./_components/spinr-pass-area-tab";
import AirportZonesTab from "./_components/airport-zones-tab";

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
                        <AirportZonesTab
                          area={area}
                          subRegions={subRegions}
                          themeV2Enabled={themeV2Enabled}
                          addAirportFor={addAirportFor}
                          setAddAirportFor={setAddAirportFor}
                          airportForm={airportForm}
                          setAirportForm={setAirportForm}
                          airportMapKey={airportMapKey}
                          setAirportMapKey={setAirportMapKey}
                          handleCreateAirportSubRegion={handleCreateAirportSubRegion}
                          handleDelete={handleDelete}
                          handleFieldUpdate={handleFieldUpdate}
                        />
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

