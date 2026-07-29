import { create } from "zustand";

/**
 * Sidebar collapsed/expanded state — shared between Sidebar (renders the
 * nav itself, needs to know its own width) and Topbar (hosts the
 * collapse-toggle button now that it moved out of the sidebar footer into
 * the top-left of the header, per the admin-portal chrome redesign).
 *
 * Persists to localStorage under the same key the sidebar used before this
 * moved to a shared store, so an existing admin's collapsed/expanded
 * preference survives the change. The CSS variable write (--sidebar-width,
 * read by DashboardLayout's <main> margin) stays a side effect here too,
 * not duplicated in both components.
 */

const STORAGE_KEY = "spinr-sidebar-collapsed";

interface SidebarState {
    collapsed: boolean;
    toggleCollapsed: () => void;
    /** Called once on mount (Sidebar) to hydrate from localStorage —
     *  can't read localStorage during store creation (SSR). */
    hydrate: () => void;
}

function applyCssVar(collapsed: boolean) {
    if (typeof document !== "undefined") {
        document.documentElement.style.setProperty("--sidebar-width", collapsed ? "68px" : "240px");
    }
}

export const useSidebarStore = create<SidebarState>((set, get) => ({
    collapsed: false,
    toggleCollapsed: () => {
        const next = !get().collapsed;
        set({ collapsed: next });
        if (typeof window !== "undefined") localStorage.setItem(STORAGE_KEY, String(next));
        applyCssVar(next);
    },
    hydrate: () => {
        if (typeof window === "undefined") return;
        const stored = localStorage.getItem(STORAGE_KEY) === "true";
        set({ collapsed: stored });
        applyCssVar(stored);
    },
}));
