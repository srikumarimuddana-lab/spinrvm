"use client";

import { useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useAuthStore } from "@/store/authStore";
import { Sidebar } from "@/components/sidebar";
import { FeatureFlagsProvider, useFeatureFlag } from "@/hooks/useFeatureFlag";

// Reads the flag from inside FeatureFlagsProvider (useFeatureFlag needs a
// descendant of the provider, not the component that renders it) and applies
// the epic #2785 Phase 3 restyle scope class — see the `.theme-v2` rules in
// globals.css. Everything else about the shell is unchanged either way.
function DashboardShell({ children }: { children: React.ReactNode }) {
    const themeV2Enabled = useFeatureFlag("admin_theme_v2_enabled");

    return (
        <div className={`min-h-screen bg-background ${themeV2Enabled ? "theme-v2" : ""}`}>
            <Sidebar />
            <main className="transition-all duration-200 md:ml-[var(--sidebar-width,240px)]">
                <div className="p-4 pt-14 md:pt-6 md:p-8">{children}</div>
            </main>
        </div>
    );
}

export default function DashboardLayout({
    children,
}: {
    children: React.ReactNode;
}) {
    const router = useRouter();
    const pathname = usePathname();
    const { isAuthenticated, isLoading } = useAuthStore();

    useEffect(() => {
        if (!isLoading && !isAuthenticated) {
            const next = pathname && pathname !== '/login' ? `?next=${encodeURIComponent(pathname)}` : '';
            router.replace(`/login${next}`);
        }
    }, [isAuthenticated, isLoading, router, pathname]);

    if (isLoading) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-background">
                <div className="text-center">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary mx-auto mb-4"></div>
                    <p>Loading...</p>
                </div>
            </div>
        );
    }

    if (!isAuthenticated) {
        return null;
    }

    return (
        <FeatureFlagsProvider>
            <DashboardShell>{children}</DashboardShell>
        </FeatureFlagsProvider>
    );
}