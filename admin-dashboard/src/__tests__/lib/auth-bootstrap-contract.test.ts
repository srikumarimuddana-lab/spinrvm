/**
 * Source-contract test for the auth bootstrap invariant that keeps the
 * unawaited `logout(); router.push("/login")` paths (topbar.tsx) safe.
 *
 * The 2026-09-12 reuse cascades were triggered by refreshing with the refresh
 * cookie still present right after a logout. A client-side navigation to
 * /login cannot do that ONLY because the bootstrap (initAuth → silentRefresh)
 * runs from one AuthInitializer mounted in the root layout behind an
 * empty-dependency effect, and the login page has no bootstrap of its own.
 * Move that effect, give it dependencies, or add a refresh to the login page
 * and the race is back with nothing else in CI to catch it — so pin it here.
 */
import { describe, it, expect } from "vitest";
import fs from "fs";
import path from "path";

const read = (rel: string) => fs.readFileSync(path.resolve(__dirname, "..", "..", rel), "utf8");

describe("auth bootstrap contract (post-logout replay guard)", () => {
    it("mounts AuthInitializer once, in the root layout", () => {
        const layout = read("app/layout.tsx");
        expect(layout).toMatch(/import \{ AuthInitializer \} from "@\/components\/auth-initializer"/);
        expect(layout.match(/<AuthInitializer \/>/g)).toHaveLength(1);
    });

    it("runs initAuth from an empty-dependency effect (once per page load, not per navigation)", () => {
        const initializer = read("components/auth-initializer.tsx");
        expect(initializer).toMatch(/useEffect\(\(\) => \{[\s\S]*?initAuth\(\);[\s\S]*?\}, \[\]\);/);
    });

    it("the login page has no bootstrap of its own", () => {
        const login = read("app/login/page.tsx");
        expect(login).not.toMatch(/initAuth|silentRefresh|AuthInitializer/);
    });

    it("initAuth itself is once-per-page-lifetime", () => {
        const store = read("store/authStore.ts");
        expect(store).toMatch(/initAuth: async \(\) => \{\s*if \(_authInitialized\) return;\s*_authInitialized = true;/);
    });
});
