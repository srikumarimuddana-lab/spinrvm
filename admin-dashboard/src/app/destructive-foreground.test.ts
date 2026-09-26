/**
 * Guard: `text-destructive-foreground` must resolve to a real colour. It is
 * used by every error toast and by red buttons in 23 components; without the
 * theme token the class generates nothing and the text inherits the page's
 * near-black foreground, 3.67:1 on red (WCAG 2.1 SC 1.4.3 needs 4.5:1).
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";

const css = readFileSync(join(__dirname, "globals.css"), "utf8");

function block(selector: string): string {
    const start = css.indexOf(`${selector} {`);
    expect(start).toBeGreaterThanOrEqual(0);
    return css.slice(start, css.indexOf("\n}", start));
}

function luminance(hex: string): number {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
        .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
    const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
}

describe("globals.css destructive-foreground token", () => {
    it("maps the Tailwind colour to the token", () => {
        expect(block("@theme inline")).toContain("--color-destructive-foreground: var(--destructive-foreground);");
    });

    it.each([":root", ".dark"])("defines a readable foreground on red in %s", (selector) => {
        const theme = block(selector);
        const fg = theme.match(/--destructive-foreground:\s*(#[0-9a-fA-F]{6})/)?.[1];
        const bg = theme.match(/\n\s*--destructive:\s*(#[0-9a-fA-F]{6})/)?.[1];
        expect(fg).toBeDefined();
        expect(bg).toBeDefined();
        expect(contrast(fg!, bg!)).toBeGreaterThanOrEqual(4.5);
    });
});
