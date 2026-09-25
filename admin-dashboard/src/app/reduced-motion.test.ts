/**
 * Guard: the admin stylesheet keeps its Reduce Motion rule (WCAG 2.1 SC
 * 2.3.3). jsdom can't evaluate media queries, so this checks the rule's
 * presence and shape statically.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";

const css = readFileSync(join(__dirname, "globals.css"), "utf8");
const block = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));

describe("globals.css Reduce Motion rule", () => {
    it("exists", () => {
        expect(css).toContain("@media (prefers-reduced-motion: reduce)");
    });

    it("shortens animations and transitions", () => {
        expect(block).toMatch(/animation-duration:\s*0\.01ms\s*!important/);
        expect(block).toMatch(/transition-duration:\s*0\.01ms\s*!important/);
    });

    it("keeps loading spinners turning", () => {
        expect(block).toContain("*:not(.animate-spin)");
    });
});
