/**
 * On-surface debug channel for the Android Auto car screen.
 *
 * Why this exists: a head unit has no dev menu, no red box, no Metro console,
 * and `adb logcat` needs the phone tethered to a laptop while sitting in the
 * car. Worse, `register.ts`'s own `log()` was gated behind `__DEV__`, so every
 * diagnostic it emitted — template creation failures, hand-off errors, the
 * degraded "no car support" path — was compiled out of exactly the release
 * builds that can run on a head unit. A failure there produced a blank screen
 * and nothing else.
 *
 * This store is the channel that makes those visible ON the car screen:
 *   - register.ts / carSurface.tsx  → `pushDebug(...)` / `setDebugFact(...)`
 *   - carSurface.tsx               → renders the panel when `enabled`
 *   - the template's idle header    → toggles `enabled`
 *
 * Same pattern as carMapCamera.ts: the surface is non-interactive, so anything
 * the driver can trigger has to come from a template button, and a store is the
 * only channel between that callback and the rendered React tree.
 *
 * Kept out of production builds by `isCarDebugAvailable` — this is a testing
 * affordance, not driver-facing UI.
 */
import { create } from 'zustand';

/** Most recent lines kept. Enough to span a connect → template → render cycle. */
export const MAX_DEBUG_LINES = 40;

export type DebugLevel = 'info' | 'error';

export interface DebugLine {
  /** ms since epoch; rendered as a relative age so the panel stays narrow. */
  at: number;
  level: DebugLevel;
  text: string;
}

/**
 * Whether the Debug header action is offered at all.
 *
 * OFF by default. The car screen is a driver's working surface, not a console —
 * once the integration is behaving, a Debug button in the header is clutter on a
 * screen that should stay glanceable, and the header is competing for room with
 * the leg's progress action and SOS.
 *
 * Flip this to `process.env.EXPO_PUBLIC_ENV !== 'production'` to bring it back
 * for a testing session. Everything behind it stays wired: `pushDebug` /
 * `setDebugFact` keep recording into the ring buffer regardless, so the moment
 * it is re-enabled the panel has real history rather than starting empty.
 *
 * Deliberately not keyed on `__DEV__`: that is precisely the flag that hid
 * register.ts's diagnostics from every build capable of reaching a head unit.
 */
const DEBUG_PANEL_ENABLED = false;

export const isCarDebugAvailable = (): boolean =>
  DEBUG_PANEL_ENABLED && process.env.EXPO_PUBLIC_ENV !== 'production';

interface CarDebugState {
  enabled: boolean;
  lines: DebugLine[];
  /** Point-in-time state (key → value), rendered as a table above the log. */
  facts: Record<string, string>;
  toggle: () => void;
  setEnabled: (enabled: boolean) => void;
  push: (level: DebugLevel, text: string) => void;
  setFact: (key: string, value: string) => void;
  clear: () => void;
}

export const useCarDebug = create<CarDebugState>((set) => ({
  enabled: false,
  lines: [],
  facts: {},
  toggle: () => set((s) => ({ enabled: !s.enabled })),
  setEnabled: (enabled) => set({ enabled }),
  push: (level, text) =>
    set((s) => ({
      // Newest first: the panel is height-limited and the latest line is the
      // one that matters when something has just failed.
      lines: [{ at: Date.now(), level, text }, ...s.lines].slice(0, MAX_DEBUG_LINES),
    })),
  setFact: (key, value) =>
    set((s) => (s.facts[key] === value ? s : { facts: { ...s.facts, [key]: value } })),
  clear: () => set({ lines: [], facts: {} }),
}));

/**
 * Record a line. Safe to call from anywhere in the car layer, including module
 * scope and native callbacks — never throws, so a logging bug can't take down
 * the surface it exists to diagnose.
 */
export const pushDebug = (level: DebugLevel, ...parts: unknown[]): void => {
  try {
    const text = parts
      .map((p) => {
        if (typeof p === 'string') return p;
        if (p instanceof Error) return `${p.name}: ${p.message}`;
        try {
          return JSON.stringify(p);
        } catch {
          return String(p);
        }
      })
      .join(' ');
    useCarDebug.getState().push(level, text);
  } catch {
    // Swallowing is correct here and only here: this is the diagnostic channel
    // itself, and it must never be the reason the car screen fails.
  }
};

/** Record a point-in-time value shown in the facts table. */
export const setDebugFact = (key: string, value: string): void => {
  try {
    useCarDebug.getState().setFact(key, value);
  } catch {
    // See pushDebug.
  }
};
