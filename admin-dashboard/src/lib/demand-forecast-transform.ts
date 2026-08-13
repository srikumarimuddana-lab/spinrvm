// Transform for GET /api/admin/analytics/demand-forecast.
//
// The response shape is known and stable (backend/utils/demand_forecast.py):
//   { hours_ahead, area_id, forecast: [
//       { timestamp, hour, day_name, predicted_rides, data_basis, is_peak }
//   ]}
//
// An earlier version of the Unmet Demand section guessed at
// `predicted_demand ?? demand` and `hour ?? time`, none of which the backend
// emits. Every bar therefore rendered at the minimum height with a tooltip
// reading "0 predicted", regardless of the real forecast — a shipped feature
// that looked functional and conveyed nothing. Speculative key fallbacks are
// deliberately NOT reintroduced here: they turn a loud breakage into a silent
// one, which is exactly how that bug survived review.

export interface ForecastSlot {
  /** Hour of day, 0-23, as returned by the backend. */
  hour: number;
  /** Predicted ride count for that hour. */
  predictedRides: number;
  /** Short axis label, e.g. "14:00". */
  label: string;
  /** Backend's own peak marker, used to accent the bar. */
  isPeak: boolean;
}

export interface ForecastResponse {
  forecast?: Array<{
    hour?: number;
    day_name?: string;
    predicted_rides?: number;
    is_peak?: boolean;
  }>;
}

export function toForecastSlots(res: ForecastResponse | null | undefined): ForecastSlot[] {
  const raw = res?.forecast;
  if (!Array.isArray(raw)) return [];
  return raw.map((s) => {
    const hour = typeof s.hour === "number" ? s.hour : 0;
    const predicted = typeof s.predicted_rides === "number" ? s.predicted_rides : 0;
    return {
      hour,
      predictedRides: predicted,
      label: `${String(hour).padStart(2, "0")}:00`,
      isPeak: Boolean(s.is_peak),
    };
  });
}

/** Bar height as a percentage of the tallest slot, with a visible floor. */
export function forecastBarHeightPct(slot: ForecastSlot, slots: ForecastSlot[]): number {
  const max = Math.max(...slots.map((s) => s.predictedRides), 0);
  if (max <= 0) return 2; // flat, honest "no predicted demand" baseline
  return Math.max(4, (slot.predictedRides / max) * 100);
}
