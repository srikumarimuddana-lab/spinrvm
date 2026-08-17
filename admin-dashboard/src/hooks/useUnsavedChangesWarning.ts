"use client";

import { useEffect } from "react";

/**
 * Warn before leaving a page with unsaved edits.
 *
 * Written for the global heatmap config tab, which is the admin surface with
 * the widest blast radius per keystroke: its values apply to every service area
 * and reach every online driver on their next refresh. Typing a new privacy
 * floor and then closing the tab lost the work silently, with nothing to
 * suggest anything had been pending.
 *
 * `beforeunload` only covers leaving the *document* — a reload, a close, a
 * navigation to another origin. Next's client-side routing does not fire it, so
 * moving between dashboard pages still discards silently; that needs a router
 * interception this codebase does not currently have anywhere, and adding one
 * here alone would be inconsistent. Covering the browser-level exits is the
 * portion that works reliably, and the limitation is stated rather than implied.
 *
 * Browsers ignore any custom message and show their own generic prompt, so none
 * is passed.
 */
export function useUnsavedChangesWarning(hasUnsavedChanges: boolean): void {
  useEffect(() => {
    if (!hasUnsavedChanges) return;

    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      // Legacy browsers require returnValue to be set for the prompt to show.
      e.returnValue = "";
      return "";
    };

    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [hasUnsavedChanges]);
}
