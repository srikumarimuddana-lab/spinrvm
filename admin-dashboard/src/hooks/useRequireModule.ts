"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/authStore";

/**
 * Enforce module-level RBAC on a client-side page.
 *
 * Usage — call at the top of the page component:
 *   const { allowed } = useRequireModule("staff");
 *   if (!allowed) return null;   // hook handles redirect
 *
 * Rules (mirrors sidebar.tsx logic and backend require_module dependency):
 *   - super_admin / admin roles → always allowed (full access)
 *   - any other role → must have `module` in user.modules
 *   - unauthenticated → redirected to /login
 *
 * Why a hook instead of middleware: the admin dashboard uses client-side
 * Zustand auth state; Next.js edge middleware can only verify the JWT
 * signature, not the modules array (a custom claim). This hook is the
 * authoritative client-side enforcement layer. The backend API also
 * enforces `require_module` on every sensitive endpoint, so even a direct
 * API call from a browser console would be rejected.
 */
export function useRequireModule(module: string): { allowed: boolean } {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  // isLoading starts true; initAuth() sets it to false once auth state is resolved.
  const isLoading = useAuthStore((s) => s.isLoading);

  const isSuperAdmin =
    user?.role === "super_admin" || user?.role === "admin";

  const hasModule =
    isSuperAdmin || (user?.modules ?? []).includes(module);

  useEffect(() => {
    if (isLoading) return;

    if (!user) {
      router.replace("/login");
      return;
    }

    if (!hasModule) {
      router.replace("/403");
    }
  }, [isLoading, user, hasModule, router]);

  // Return allowed=false until auth is resolved to prevent a flash of
  // protected content while the redirect is in flight.
  if (isLoading || !user || !hasModule) {
    return { allowed: false };
  }

  return { allowed: true };
}
