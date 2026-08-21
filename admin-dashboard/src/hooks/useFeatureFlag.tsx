"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { useAuthStore } from "@/store/authStore";
import { getSettings } from "@/lib/api";

/**
 * Feature flags read from the app_settings-backed GET /api/admin/settings
 * endpoint (60s in-process cache on the backend — see settings_loader.py).
 *
 * Explicit allowlist rather than spreading the raw settings response: that
 * endpoint also returns masked credential fields (Stripe/Twilio/etc keys),
 * which have no business living in a context every component can read.
 * Add new flags here as they're introduced (epic #2785 Phase 3+).
 */
type FlagKey = "admin_theme_v2_enabled";
const FLAG_KEYS: FlagKey[] = ["admin_theme_v2_enabled"];

const DEFAULTS: Record<FlagKey, boolean> = {
  admin_theme_v2_enabled: false,
};

const FeatureFlagsContext = createContext<Record<FlagKey, boolean>>(DEFAULTS);

/**
 * Mount once, inside the authenticated dashboard layout (not the root
 * layout — GET /api/admin/settings is admin-auth-gated, so fetching it
 * before login just wastes a 401). Fails safe to all-flags-off if the
 * fetch errors; never blocks rendering on this.
 */
export function FeatureFlagsProvider({ children }: { children: ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const [flags, setFlags] = useState<Record<FlagKey, boolean>>(DEFAULTS);

  useEffect(() => {
    if (!isAuthenticated) return;
    let cancelled = false;

    getSettings()
      .then((data) => {
        if (cancelled) return;
        const next = { ...DEFAULTS };
        for (const key of FLAG_KEYS) next[key] = !!data?.[key];
        setFlags(next);
      })
      .catch(() => {
        // Fail safe: leave flags at their default (off) rather than
        // throwing or retrying — a broken settings fetch must never take
        // down the dashboard shell.
      });

    return () => {
      cancelled = true;
    };
  }, [isAuthenticated]);

  return <FeatureFlagsContext.Provider value={flags}>{children}</FeatureFlagsContext.Provider>;
}

export function useFeatureFlag(key: FlagKey): boolean {
  // PREVIEW-ONLY OVERRIDE — DO NOT MERGE. Forces every flag on regardless of
  // the live app_settings row, so this branch's Vercel preview renders
  // Phase 3 (epic #2785) for visual review without touching production
  // data. See docs/change-log/2026-08-21-phase3-preview-branch.md.
  // Real implementation, restore on revert:
  //   const flags = useContext(FeatureFlagsContext);
  //   return flags[key] ?? false;
  void key;
  return true;
}
