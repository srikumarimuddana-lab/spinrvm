"use client";

import { useEffect, useState } from "react";
import { CheckCircle } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { getSettings, updateSettings } from "@/lib/api/settings-ai";
import { parseAllowlistIds } from "@/lib/allowlist-ids";
import { useUnsavedChangesWarning } from "@/hooks/useUnsavedChangesWarning";

// ─── Heatmap Config Tab (global app_settings — AD-05) ───
// Extracted verbatim from service-areas/page.tsx.

const HEATMAP_DEFAULTS = {
  // Master switch defaults ON so an unconfigured row behaves exactly as before
  // the setting existed; the per-area toggle still governs.
  driver_heatmap_enabled: true,
  driver_heatmap_v2_enabled: false,
  heatmap_internal_driver_ids: [] as string[],
  heatmap_k_floor: 3,
  heatmap_cell_lat_deg: 0.004,
  heatmap_cell_lng_deg: 0.006,
  heatmap_decay_half_life_days: 3,
  heatmap_refresh_seconds: 90,
};

export default function HeatmapConfigTab({ onSuccess, onError }: { onSuccess: () => void; onError: (e: unknown) => void }) {
  const [form, setForm] = useState(HEATMAP_DEFAULTS);
  const [driverIdsText, setDriverIdsText] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  // This is the one tab whose values apply to every service area and reach
  // every online driver on their next refresh, so losing an edit to a stray
  // reload is worth a prompt. Covers browser-level exits only — see the hook.
  useUnsavedChangesWarning(dirty);

  useEffect(() => {
    getSettings().then((s: any) => {
      const next = { ...HEATMAP_DEFAULTS };
      if (s.driver_heatmap_v2_enabled !== undefined) next.driver_heatmap_v2_enabled = !!s.driver_heatmap_v2_enabled;
      if (s.driver_heatmap_enabled !== undefined) next.driver_heatmap_enabled = Boolean(s.driver_heatmap_enabled);
      if (s.heatmap_k_floor !== undefined) next.heatmap_k_floor = Number(s.heatmap_k_floor);
      if (s.heatmap_cell_lat_deg !== undefined) next.heatmap_cell_lat_deg = Number(s.heatmap_cell_lat_deg);
      if (s.heatmap_cell_lng_deg !== undefined) next.heatmap_cell_lng_deg = Number(s.heatmap_cell_lng_deg);
      if (s.heatmap_decay_half_life_days !== undefined) next.heatmap_decay_half_life_days = Number(s.heatmap_decay_half_life_days);
      if (s.heatmap_refresh_seconds !== undefined) next.heatmap_refresh_seconds = Number(s.heatmap_refresh_seconds);
      const ids = Array.isArray(s.heatmap_internal_driver_ids) ? s.heatmap_internal_driver_ids : [];
      next.heatmap_internal_driver_ids = ids;
      setForm(next);
      setDriverIdsText(ids.join("\n"));
      setLoadError(null);
      setLoading(false);
    }).catch((e: any) => {
      // Do NOT fall through to an editable form pre-filled with defaults. It is
      // visually identical to a successful load, so an operator who edits one
      // field and saves would silently overwrite the real k-anonymity floor and
      // dark-launch allowlist with hardcoded defaults.
      console.error("heatmap config load failed", e);
      setLoadError(
        e?.status === 403
          ? "You don't have the Settings module, so heatmap config can't be loaded or changed here."
          : "Couldn't load the current heatmap config. Refresh to retry — editing is disabled to avoid overwriting live settings with defaults."
      );
      setLoading(false);
    });
  }, []);

  const set = <K extends keyof typeof HEATMAP_DEFAULTS>(key: K, val: (typeof HEATMAP_DEFAULTS)[K]) => {
    setForm(prev => ({ ...prev, [key]: val }));
    setDirty(true);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      // One parser, shared with the count shown below the field. They used to
      // split differently, so a space-separated paste displayed as 1 ID and
      // saved as several.
      const { ids } = parseAllowlistIds(driverIdsText);
      await updateSettings({
        driver_heatmap_enabled: form.driver_heatmap_enabled,
        driver_heatmap_v2_enabled: form.driver_heatmap_v2_enabled,
        heatmap_internal_driver_ids: ids,
        heatmap_k_floor: form.heatmap_k_floor,
        heatmap_cell_lat_deg: form.heatmap_cell_lat_deg,
        heatmap_cell_lng_deg: form.heatmap_cell_lng_deg,
        heatmap_decay_half_life_days: form.heatmap_decay_half_life_days,
        heatmap_refresh_seconds: form.heatmap_refresh_seconds,
      });
      setForm(prev => ({ ...prev, heatmap_internal_driver_ids: ids }));
      setDirty(false);
      onSuccess();
    } catch (e) {
      onError(e);
    }
    setSaving(false);
  };

  if (loading) return <div className="py-10 text-center text-muted-foreground">Loading heatmap config…</div>;

  if (loadError) {
    return (
      <div role="alert" className="rounded-xl border border-destructive/40 bg-destructive/5 p-4">
        <p className="text-sm font-semibold text-destructive">Heatmap config unavailable</p>
        <p className="mt-1 text-sm text-muted-foreground">{loadError}</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h4 className="font-bold text-foreground text-base">Driver App Heatmap — Platform Settings</h4>
        {/* This panel sits inside one service area's card but every control here
            is PLATFORM-WIDE, and it governs what the DRIVER APP shows — not this
            dashboard's own Heat Map page. Both facts have to be unmissable:
            an operator who reads this as "tuning Saskatoon's display" can change
            what every driver in every region sees in one click. */}
        <div className="mt-2 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2">
          <p className="text-sm text-warning">
            <span className="font-semibold">Applies to all service areas and all drivers.</span>{" "}
            These settings control the demand heatmap inside the <span className="font-semibold">driver app</span>,
            not the Heat Map page in this dashboard. They are not per-area.
          </p>
        </div>
      </div>

      {/* Global kill switch */}
      <div className="flex items-center justify-between p-4 rounded-xl border bg-card">
        <div className="pr-4">
          <label htmlFor="heatmap-master-enabled" className="text-sm font-semibold text-foreground">
            Driver Heatmap Enabled
          </label>
          <p className="text-xs text-muted-foreground mt-0.5">
            Master switch. Turning this off hides the heatmap for every driver in every
            region within one refresh, regardless of each area&apos;s own toggle. Use this to
            take the feature down without editing areas one by one.
          </p>
        </div>
        <Switch
          id="heatmap-master-enabled"
          checked={form.driver_heatmap_enabled}
          onCheckedChange={(v: boolean) => set('driver_heatmap_enabled', v)}
        />
      </div>

      {/* v2 enabled toggle */}
      <div className="flex items-center justify-between p-4 rounded-xl border bg-card">
        <div className="pr-4">
          <label htmlFor="heatmap-v2-enabled" className="text-sm font-semibold text-foreground">
            Heatmap v2 Enabled
          </label>
          <p className="text-xs text-muted-foreground mt-0.5">
            When off, drivers see the legacy v1 heatmap (simple points). When on, drivers get
            cell-based demand layers, forecast, and hotspot chips.
          </p>
        </div>
        <Switch
          id="heatmap-v2-enabled"
          checked={form.driver_heatmap_v2_enabled}
          onCheckedChange={(v: boolean) => set('driver_heatmap_v2_enabled', v)}
        />
      </div>

      {/* Numeric config */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        <HeatmapNumericField id="heatmap-k-floor" label="Privacy floor (k-anonymity)" description="Minimum ride count per cell before it appears on the heatmap. Higher = more private, fewer visible cells."
          value={form.heatmap_k_floor} min={1} max={50} step={1} disabled={!!loadError}
          onChange={v => set('heatmap_k_floor', v)} />
        <HeatmapNumericField id="heatmap-cell-lat" label="Cell latitude (degrees)" description="Height of each grid cell. Default 0.004° ≈ 445m."
          value={form.heatmap_cell_lat_deg} min={0.0005} max={0.05} step={0.001} disabled={!!loadError}
          onChange={v => set('heatmap_cell_lat_deg', v)} />
        <HeatmapNumericField id="heatmap-cell-lng" label="Cell longitude (degrees)" description="Width of each grid cell. Default 0.006° ≈ 430m at 52°N."
          value={form.heatmap_cell_lng_deg} min={0.0005} max={0.05} step={0.001} disabled={!!loadError}
          onChange={v => set('heatmap_cell_lng_deg', v)} />
        <HeatmapNumericField id="heatmap-decay" label="Decay half-life (days)" description="Ride weighting decay. Rides from this many days ago count as half."
          value={form.heatmap_decay_half_life_days} min={0.5} max={30} step={0.5} disabled={!!loadError}
          onChange={v => set('heatmap_decay_half_life_days', v)} />
        <HeatmapNumericField id="heatmap-refresh" label="Refresh interval (seconds)" description="How often the driver app re-fetches heatmap data. Applies to every online driver."
          value={form.heatmap_refresh_seconds} min={30} max={600} step={10} disabled={!!loadError}
          onChange={v => set('heatmap_refresh_seconds', v)} />
      </div>

      {/* Internal driver IDs */}
      <div className="p-4 rounded-xl border bg-card">
        <label htmlFor="heatmap-driver-allowlist" className="block text-sm font-semibold text-foreground mb-1">
          Dark-launch allowlist (driver user IDs)
        </label>
        {/* Backend semantics (routes/drivers/profile.py):
              v2_enabled = v2_global OR (user_id in allowlist)
            i.e. this list GRANTS early access while the global flag is off; it
            does NOT restrict who sees v2 once the flag is on. The previous copy
            said the opposite, so an operator flipping the flag believing this
            list limited the blast radius would in fact release v2 to everyone. */}
        <p className="text-xs text-muted-foreground mb-2">
          These drivers see v2 <span className="font-semibold">even while the switch above is off</span> —
          use it to test with staff before release. It does not limit anyone once v2 is on:
          with v2 enabled, every driver sees it regardless of this list.
          One ID per line or comma-separated. Use the <span className="font-semibold">user</span> ID
          (users.id), not the driver record ID.
        </p>
        <textarea
          id="heatmap-driver-allowlist"
          className="w-full border rounded-lg px-3 py-2 text-sm font-mono"
          rows={4}
          value={driverIdsText}
          onChange={e => { setDriverIdsText(e.target.value); setDirty(true); }}
          placeholder="e.g. abc-123-def&#10;ghi-456-jkl"
        />
        {driverIdsText.trim() && (() => {
          const { ids, invalid } = parseAllowlistIds(driverIdsText);
          return (
            <>
              <p className="text-xs text-muted-foreground mt-1">
                {ids.length} driver ID(s) in allowlist
              </p>
              {invalid.length > 0 && (
                // Surfaced, not dropped: the allowlist decides who gets v2
                // during a dark launch, so an entry that matches nothing makes
                // the rollout look like it did nothing.
                <p role="alert" className="text-xs text-warning mt-1">
                  {invalid.length} entr{invalid.length === 1 ? "y does" : "ies do"} not look like a
                  user ID and will match no driver: {invalid.slice(0, 3).join(", ")}
                  {invalid.length > 3 ? "…" : ""}
                </p>
              )}
            </>
          );
        })()}
      </div>

      {/* Save button */}
      <div className="flex items-center gap-3">
        <button
          onClick={handleSave}
          disabled={!dirty || saving}
          className={`px-6 py-2.5 rounded-xl text-sm font-semibold transition ${dirty ? 'bg-primary text-primary-foreground hover:bg-primary/90' : 'bg-muted text-muted-foreground cursor-not-allowed'}`}
        >
          {saving ? 'Saving…' : dirty ? 'Save Heatmap Config' : 'Saved'}
        </button>
        {!dirty && !saving && <span className="text-xs text-success flex items-center gap-1"><CheckCircle className="h-3.5 w-3.5" /> Up to date</span>}
      </div>
    </div>
  );
}

function HeatmapNumericField({ id, label, description, value, min, max, step, disabled, onChange }: {
  id: string; label: string; description: string; value: number; min: number; max: number; step: number;
  disabled?: boolean; onChange: (v: number) => void;
}) {
  const describedBy = `${id}-desc`;
  return (
    <div className="p-3 rounded-lg border bg-card">
      {/* htmlFor/id is load-bearing, not decoration: without it a screen reader
          announces five adjacent "spin button"s with no way to tell which
          setting is which. */}
      <label htmlFor={id} className="block text-sm font-semibold text-foreground mb-0.5">{label}</label>
      <p id={describedBy} className="text-xs text-muted-foreground mb-2">{description}</p>
      <input
        id={id}
        aria-describedby={describedBy}
        type="number" min={min} max={max} step={step}
        value={value}
        disabled={disabled}
        // Clamp on the way out. HTML min/max do not constrain typed values, and
        // these feed a driver-fleet poll interval and a privacy floor — a typed
        // 9 instead of 90 would be a self-inflicted load spike.
        onChange={e => {
          const parsed = parseFloat(e.target.value);
          if (Number.isNaN(parsed)) return;
          onChange(Math.min(max, Math.max(min, parsed)));
        }}
        className="w-full border rounded-lg px-3 py-2 text-sm disabled:opacity-60"
      />
    </div>
  );
}
