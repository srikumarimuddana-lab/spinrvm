"use client";

import { useEffect, useState } from "react";
import { getAreaHeatmapConfig, updateServiceArea } from "@/lib/api";
import type { AreaHeatmapConfig } from "@/lib/api";
import { useUnsavedChangesWarning } from "@/hooks/useUnsavedChangesWarning";
import { TUNING_PLAYBOOK, tuningWarnings, warningsFor } from "@/lib/heatmap-tuning-guidance";

// ─── Heatmap Config Tab (global app_settings — AD-05) ───
// Extracted verbatim from service-areas/page.tsx.

// Human labels for the per-area tuning keys. The bounds themselves are served
// by the backend (from HEATMAP_SPEC), so adding a knob there surfaces it here
// automatically with correct limits — only the prose lives in the frontend.
const HEATMAP_KEY_LABELS: Record<string, { label: string; help: string }> = {
  k_floor: {
    label: "Privacy floor (k-anonymity)",
    help: "Minimum rides in a cell before it may appear. A low-volume area may need a longer baseline window instead of a lower floor — never trade privacy for coverage.",
  },
  cell_lat_deg: { label: "Cell height (degrees)", help: "0.004° ≈ 445 m. Smaller cells show finer detail but need more rides to clear the privacy floor." },
  cell_lng_deg: { label: "Cell width (degrees)", help: "0.006° ≈ 430 m at 52°N." },
  decay_half_life_days: { label: "Decay half-life (days)", help: "Rides this old count half as much, so 'busy now' outranks 'was busy last week'." },
  refresh_seconds: { label: "Refresh interval (seconds)", help: "How often each driver's app re-fetches. Applies to every online driver in this area." },
  live_window_days: { label: "Live window (days)", help: "Lookback for the decayed demand cloud." },
  now_window_minutes: { label: "'Busy now' window (minutes)", help: "How recent a ride must be to count as demand right now." },
  baseline_window_days: { label: "'Usually busy' window (days)", help: "History used for the same-hour-of-week baseline. Quieter areas benefit from a longer window." },
  scheduled_lookahead_hours: { label: "Scheduled lookahead (hours)", help: "How far ahead booked pickups count as demand." },
  forecast_hours_ahead: { label: "Forecast horizon (hours)", help: "How far ahead the driver forecast strip projects." },
  forecast_lookback_days: { label: "Forecast history (days)", help: "History the forecast is built from." },
};

/** Per-area overrides for the heatmap tuning knobs.
 *
 * Sits above the global panel in the same tab so the relationship is visible:
 * anything not overridden here follows the platform value shown below. Each
 * row is explicitly "inherits X" or "overridden", because those look identical
 * once saved but diverge the moment the global changes. */
export function AreaHeatmapOverrides({
  areaId,
  areaName,
  onSuccess,
  onError,
}: {
  areaId: string;
  areaName: string;
  onSuccess: () => void;
  onError: (e: unknown) => void;
}) {
  const [cfg, setCfg] = useState<AreaHeatmapConfig | null>(null);
  const [draft, setDraft] = useState<Record<string, number | undefined>>({});
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  // Narrower blast radius than the global tab (one area), but the same silent
  // loss on a stray reload.
  useUnsavedChangesWarning(dirty);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    getAreaHeatmapConfig(areaId)
      .then((res) => {
        if (cancelled) return;
        setCfg(res);
        setDraft({ ...res.overrides });
        setDirty(false);
        setLoading(false);
      })
      .catch((e: any) => {
        if (cancelled) return;
        // Same reasoning as the global panel: never fall through to an
        // editable form showing defaults, or a save would overwrite real
        // per-area tuning with values the operator never actually saw.
        console.error("area heatmap config load failed", e);
        setLoadError(
          e?.status === 403
            ? "You don't have the Service Areas module, so per-area tuning can't be loaded."
            : "Couldn't load this area's tuning. Refresh to retry — editing is disabled to avoid overwriting it."
        );
        setLoading(false);
      });
    return () => { cancelled = true; };
  }, [areaId]);

  const setKey = (key: string, value: number | undefined) => {
    setDraft((prev) => {
      const next = { ...prev };
      if (value === undefined) delete next[key];
      else next[key] = value;
      return next;
    });
    setDirty(true);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      // Sends the full override set (not a patch): an omitted key means
      // "inherit", which is exactly how clearing one is expressed.
      await updateServiceArea(areaId, { heatmap_config: draft });
      const fresh = await getAreaHeatmapConfig(areaId);
      setCfg(fresh);
      setDraft({ ...fresh.overrides });
      setDirty(false);
      onSuccess();
    } catch (e) {
      onError(e);
    }
    setSaving(false);
  };

  if (loading) return <div className="py-6 text-center text-muted-foreground">Loading {areaName} tuning…</div>;

  if (loadError) {
    return (
      <div role="alert" className="rounded-xl border border-destructive/40 bg-destructive/5 p-4">
        <p className="text-sm font-semibold text-destructive">Per-area tuning unavailable</p>
        <p className="mt-1 text-sm text-muted-foreground">{loadError}</p>
      </div>
    );
  }
  if (!cfg) return null;

  const overriddenCount = Object.keys(draft).length;
  // Evaluated against the draft, so a risky value is flagged while it's being
  // typed rather than discovered after it reaches every driver in the area.
  const warnings = tuningWarnings(draft, cfg.inherited);

  return (
    <div className="space-y-4 pb-6 mb-6 border-b">
      <div>
        <h4 className="font-bold text-foreground text-base">{areaName} — Heatmap Tuning</h4>
        <p className="text-sm text-muted-foreground mt-0.5">
          Applies to <span className="font-semibold">{areaName} only</span>. Anything left on
          &ldquo;inherit&rdquo; follows the platform settings below.{" "}
          {overriddenCount > 0
            ? `${overriddenCount} value${overriddenCount === 1 ? "" : "s"} overridden.`
            : "No overrides — this area follows the platform settings."}
        </p>
      </div>

      <details className="rounded-lg border bg-muted/30">
        <summary className="cursor-pointer px-3 py-2 text-sm font-semibold text-foreground">
          What should I change? — tuning playbook
        </summary>
        <div className="border-t px-3 py-2">
          <p className="text-xs text-muted-foreground">
            Start from the symptom, not the knob. Every change here is a trade — there is no
            setting that improves the map for free.
          </p>
          <dl className="mt-2 space-y-2.5">
            {TUNING_PLAYBOOK.map((entry) => (
              <div key={entry.symptom}>
                <dt className="text-xs font-semibold text-foreground">{entry.symptom}</dt>
                <dd className="text-xs text-muted-foreground">
                  {entry.action}{" "}
                  <span className="italic">Trade-off: {entry.tradeoff}</span>
                </dd>
              </div>
            ))}
          </dl>
        </div>
      </details>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        {Object.entries(cfg.spec).map(([key, spec]) => {
          const meta = HEATMAP_KEY_LABELS[key] ?? { label: key, help: "" };
          const isOverridden = key in draft;
          const inherited = cfg.inherited[key];
          const inputId = `area-hm-${areaId}-${key}`;
          return (
            <div key={key} className="p-3 rounded-lg border bg-card">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <label htmlFor={inputId} className="block text-sm font-semibold text-foreground">
                    {meta.label}
                  </label>
                  <p id={`${inputId}-help`} className="text-xs text-muted-foreground mt-0.5">{meta.help}</p>
                </div>
                <label className="flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={isOverridden}
                    onChange={(e) => setKey(key, e.target.checked ? (inherited ?? spec.default) : undefined)}
                    aria-label={`Override ${meta.label} for ${areaName}`}
                  />
                  Override
                </label>
              </div>
              <div className="mt-2 flex items-center gap-2">
                <input
                  id={inputId}
                  aria-describedby={`${inputId}-help`}
                  type="number"
                  min={spec.min}
                  max={spec.max}
                  step={spec.kind === "int" ? 1 : 0.001}
                  disabled={!isOverridden}
                  value={isOverridden ? draft[key] : (inherited ?? spec.default)}
                  onChange={(e) => {
                    const parsed = parseFloat(e.target.value);
                    if (Number.isNaN(parsed)) return;
                    // Clamp on the way out: HTML min/max don't constrain typed
                    // values, and the backend rejects out-of-range with a 422.
                    setKey(key, Math.min(spec.max, Math.max(spec.min, parsed)));
                  }}
                  className="w-32 border rounded-lg px-3 py-1.5 text-sm disabled:opacity-60 disabled:bg-muted"
                />
                <span className="text-xs text-muted-foreground">
                  {isOverridden
                    ? `platform: ${inherited ?? spec.default}`
                    : `inherits ${inherited ?? spec.default}`}
                  <span className="ml-1 opacity-70">({spec.min}–{spec.max})</span>
                </span>
              </div>
              {warningsFor(key, warnings).map((w) => (
                <p
                  key={`${key}-${w.severity}`}
                  // role=alert only for the ones that carry a real cost — an
                  // informational note firing a screen-reader interruption on
                  // every keystroke would be worse than silence.
                  role={w.severity === "warning" ? "alert" : undefined}
                  className={`mt-2 rounded-md border px-2 py-1.5 text-xs ${
                    w.severity === "warning"
                      ? "border-destructive/40 bg-destructive/5 text-destructive"
                      // eslint-disable-next-line no-restricted-syntax -- lower-severity ("info") tier below "warning"; no dedicated semantic token exists for it (#2816)
                      : "border-amber-500/40 bg-amber-500/5 text-amber-700 dark:text-amber-400"
                  }`}
                >
                  {w.message}
                </p>
              ))}
            </div>
          );
        })}
      </div>

      <div className="flex items-center gap-3">
        <button
          onClick={handleSave}
          disabled={!dirty || saving}
          className={`px-5 py-2 rounded-xl text-sm font-semibold transition ${dirty ? 'bg-primary text-primary-foreground hover:bg-primary/90' : 'bg-muted text-muted-foreground cursor-not-allowed'}`}
        >
          {saving ? 'Saving…' : dirty ? `Save ${areaName} tuning` : 'Saved'}
        </button>
        {dirty && (
          <button
            onClick={() => { setDraft({ ...cfg.overrides }); setDirty(false); }}
            className="text-xs underline text-muted-foreground hover:text-foreground"
          >
            Discard changes
          </button>
        )}
        {dirty && warnings.some((w) => w.severity === "warning") && (
          <span className="text-xs text-destructive">
            Saving applies to every driver in {areaName} on their next refresh — check the
            flagged values above.
          </span>
        )}
      </div>
    </div>
  );
}
