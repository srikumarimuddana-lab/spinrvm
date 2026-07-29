"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/authStore";

/**
 * Enforce a strict super_admin-only client-side gate.
 *
 * Usage — call at the top of the page component:
 *   const { allowed } = useRequireSuperAdmin();
 *   if (!allowed) return null;   // hook handles redirect
 *
 * Unlike useRequireModule, this does NOT treat role "admin" as equivalent
 * to "super_admin" — it mirrors the backend's `require_super_admin`
 * dependency (backend/dependencies/__init__.py), which checks
 * `role == "super_admin"` exactly. Use this (not useRequireModule) for any
 * page whose backend routes are gated by require_super_admin rather than
 * require_module — otherwise an "admin"-role user passes the client gate,
 * sees the page render, and then gets 403'd on every API call.
 */
export function useRequireSuperAdmin(): { allowed: boolean } {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  // isLoading starts true; initAuth() sets it to false once auth state is resolved.
  const isLoading = useAuthStore((s) => s.isLoading);

  const isSuperAdmin = user?.role === "super_admin";

  useEffect(() => {
    if (isLoading) return;

    if (!user) {
      router.replace("/login");
      return;
    }

    if (!isSuperAdmin) {
      router.replace("/403");
    }
  }, [isLoading, user, isSuperAdmin, router]);

  // Return allowed=false until auth is resolved to prevent a flash of
  // protected content while the redirect is in flight.
  if (isLoading || !user || !isSuperAdmin) {
    return { allowed: false };
  }

  return { allowed: true };
}
