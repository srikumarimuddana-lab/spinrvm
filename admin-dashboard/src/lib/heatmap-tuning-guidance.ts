/**
 * Operator guidance for the per-service-area heatmap tuning knobs.
 *
 * The form already shows each knob's safe range and what it inherits. What it
 * did not show is the part an operator actually arrives with: a symptom
 * ("drivers say the map is empty in Moose Jaw") and no idea which of eleven
 * numbers to touch. Worse, the two most tempting knobs are the two with real
 * costs attached — dropping the privacy floor to fill an empty map, and
 * dropping the refresh interval to make it feel live — and nothing in the form
 * pushed back.
 *
 * So this module is two things:
 *
 *   1. `TUNING_PLAYBOOK` — symptom → which knob, which direction, and the
 *      trade-off. Static prose; it just needed somewhere to live that isn't
 *      buried in JSX.
 *   2. `tuningWarnings()` — evaluated against the *draft*, so a risky value
 *      is called out while it is being typed rather than discovered later.
 *      This is the half that prevents a mistake instead of describing one.
 *
 * Kept as a pure function (no React) for the same reason `demand-bands.ts` is:
 * the decision is worth testing on its own, and the screen it renders in is
 * a ~2500-line page.
 */

export type TuningSeverity = "warning" | "info";

export interface TuningWarning {
  /** Config key the warning is attached to, so the form can render it inline. */
  key: string;
  severity: TuningSeverity;
  message: string;
}

export interface PlaybookEntry {
  /** What the operator noticed, in their words. */
  symptom: string;
  /** What to actually do about it. */
  action: string;
  /** What it costs — every one of these is a trade, none is free. */
  tradeoff: string;
}

/**
 * Symptom-first, because that is the direction an operator reads it in. Ordered
 * by how often each complaint actually reaches ops, not by config key order.
 */
export const TUNING_PLAYBOOK: PlaybookEntry[] = [
  {
    symptom: "Drivers in a quiet area see a mostly empty map",
    action:
      "Raise “Usually busy” window (baseline_window_days) — up to 90 days — and/or raise cell height/width so each cell pools more rides.",
    tradeoff:
      "A longer window blurs recent changes in where demand sits; bigger cells point drivers at a wider area, so the guidance gets vaguer.",
  },
  {
    symptom: "The map feels stale — it lags what dispatch is seeing",
    action:
      "Lower Decay half-life (recent rides outweigh old ones sooner) before touching the refresh interval.",
    tradeoff:
      "A short half-life makes the map twitchy: one cancelled ride can visibly move a cell.",
  },
  {
    symptom: "Cells are too coarse to be actionable downtown",
    action:
      "Lower cell height/width for that area only. 0.004° ≈ 445 m north–south; 0.006° ≈ 430 m east–west at 52°N.",
    tradeoff:
      "Smaller cells hold fewer rides each, so more of them fall below the privacy floor and disappear. Dense areas absorb this; quiet ones do not.",
  },
  {
    symptom: "Drivers report battery or data drain",
    action: "Raise Refresh interval for the area (up to 600 s).",
    tradeoff:
      "The map updates less often. This applies to every online driver in the area at once — it is the highest-blast-radius knob here.",
  },
  {
    symptom: "Airport/event demand shows up too late to reposition for",
    action: "Raise Scheduled lookahead so booked pickups count as demand sooner.",
    tradeoff:
      "Long lookaheads surface demand a driver cannot reach yet, which reads as a false positive.",
  },
];

/** Keys whose value carries a cost the operator should be told about. */
const K_FLOOR = "k_floor";
const REFRESH = "refresh_seconds";
const CELL_KEYS = ["cell_lat_deg", "cell_lng_deg"] as const;

/**
 * Warnings for a draft override set, evaluated against what the area would
 * otherwise inherit.
 *
 * `draft` holds only the keys being overridden — an absent key means "inherit",
 * which is never worth warning about, since the platform value is the reviewed
 * one.
 */
export function tuningWarnings(
  draft: Record<string, number | undefined>,
  inherited: Record<string, number>
): TuningWarning[] {
  const out: TuningWarning[] = [];

  const kFloor = draft[K_FLOOR];
  const inheritedFloor = inherited[K_FLOOR];
  if (kFloor !== undefined && inheritedFloor !== undefined && kFloor < inheritedFloor) {
    out.push({
      key: K_FLOOR,
      severity: "warning",
      message:
        `Privacy floor lowered below the platform value (${inheritedFloor}). This is the k-anonymity ` +
        `guarantee, not a coverage setting — a cell built from ${kFloor} ride${kFloor === 1 ? "" : "s"} ` +
        `can identify the rider who took it. To fill an empty map, widen the “Usually busy” window or ` +
        `the cell size instead.`,
    });
  }

  const refresh = draft[REFRESH];
  const inheritedRefresh = inherited[REFRESH];
  if (refresh !== undefined && inheritedRefresh !== undefined && refresh < inheritedRefresh) {
    out.push({
      key: REFRESH,
      severity: "warning",
      message:
        `Every online driver in this area will re-fetch every ${refresh}s instead of ${inheritedRefresh}s. ` +
        `That multiplies both their battery/data use and this area's share of heatmap load by ` +
        `${(inheritedRefresh / refresh).toFixed(1)}×.`,
    });
  }

  // Smaller cells + an unchanged floor is the counterintuitive one: the map
  // gets emptier, not more detailed, and it looks like the change did nothing.
  for (const key of CELL_KEYS) {
    const cell = draft[key];
    const inheritedCell = inherited[key];
    if (cell !== undefined && inheritedCell !== undefined && cell < inheritedCell) {
      out.push({
        key,
        severity: "info",
        message:
          `Smaller cells hold fewer rides each, so more of them will fall under the privacy floor and ` +
          `vanish. Expect a sparser map here, not a more detailed one, unless this area has the ride ` +
          `volume to carry it.`,
      });
    }
  }

  return out;
}

/** Convenience for rendering: the warnings attached to one key. */
export function warningsFor(key: string, warnings: TuningWarning[]): TuningWarning[] {
  return warnings.filter((w) => w.key === key);
}
