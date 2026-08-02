/**
 * Corporate + admin portal review, Admin #4: useRequireModule previously
 * bypassed for role === "admin" as well as "super_admin". "admin" is a
 * real, separate backend role that does NOT bypass require_module() on
 * the backend — only super_admin does. These tests lock in that an
 * "admin"-role user without the target module is blocked, matching the
 * backend contract, while super_admin and an explicit module grant still
 * pass.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";

const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
}));

let mockUser: { role: string; modules?: string[] } | null = null;
let mockIsLoading = false;

vi.mock("@/store/authStore", () => ({
  useAuthStore: (selector: (s: { user: typeof mockUser; isLoading: boolean }) => unknown) =>
    selector({ user: mockUser, isLoading: mockIsLoading }),
}));

import { useRequireModule } from "@/hooks/useRequireModule";

describe("useRequireModule", () => {
  beforeEach(() => {
    replace.mockClear();
    mockIsLoading = false;
  });

  it("blocks an admin-role user without the target module", () => {
    mockUser = { role: "admin", modules: ["drivers"] };
    const { result } = renderHook(() => useRequireModule("staff"));
    expect(result.current.allowed).toBe(false);
    expect(replace).toHaveBeenCalledWith("/403");
  });

  it("allows an admin-role user who has the target module granted", () => {
    mockUser = { role: "admin", modules: ["staff"] };
    const { result } = renderHook(() => useRequireModule("staff"));
    expect(result.current.allowed).toBe(true);
    expect(replace).not.toHaveBeenCalled();
  });

  it("always allows a super_admin, regardless of modules", () => {
    mockUser = { role: "super_admin", modules: [] };
    const { result } = renderHook(() => useRequireModule("staff"));
    expect(result.current.allowed).toBe(true);
    expect(replace).not.toHaveBeenCalled();
  });

  it("redirects to /login when unauthenticated", () => {
    mockUser = null;
    const { result } = renderHook(() => useRequireModule("staff"));
    expect(result.current.allowed).toBe(false);
    expect(replace).toHaveBeenCalledWith("/login");
  });
});
