/**
 * The heatmap v2 allowlist field had two different parsers for one input.
 *
 * The save path split on `/[\s,]+/`; the "N driver ID(s) in allowlist" count
 * rendered right below it split on `/[\n,]+/`. So a space-separated paste —
 * the most natural thing to do when copying IDs out of a chat message —
 * displayed as 1 ID and saved as several. The count is the only feedback an
 * operator gets before saving, and it was lying.
 *
 * This matters beyond tidiness because the allowlist decides which drivers get
 * the v2 heatmap during a dark launch. A wrong entry means the cohort silently
 * isn't enrolled and the rollout looks like it did nothing.
 */

import { describe, it, expect } from "vitest";

import { isLikelyUuid, parseAllowlistIds } from "@/lib/allowlist-ids";

const A = "3f2504e0-4f89-41d3-9a0c-0305e82c3301";
const B = "7b8e1a2c-9d4f-4b3a-8c1e-2f5a6d7b8c90";

describe("separators", () => {
  it.each([
    ["newlines", `${A}\n${B}`],
    ["commas", `${A},${B}`],
    ["spaces", `${A} ${B}`],
    ["tabs", `${A}\t${B}`],
    ["comma + space", `${A}, ${B}`],
    ["mixed and messy", `  ${A} ,\n\n ${B}  `],
  ])("parses two IDs separated by %s", (_label, raw) => {
    // Any of these is a reasonable paste. The operator should not have to know
    // which one the field wants.
    expect(parseAllowlistIds(raw).ids).toEqual([A, B]);
  });

  it("returns nothing for whitespace-only input", () => {
    expect(parseAllowlistIds("   \n\t ").ids).toEqual([]);
  });
});

describe("deduplication", () => {
  it("collapses a repeated ID", () => {
    expect(parseAllowlistIds(`${A}\n${A}`).ids).toEqual([A]);
  });

  it("treats case variants as the same driver", () => {
    // A UUID pasted from two sources can differ in case; sending it twice makes
    // the saved count misleading.
    expect(parseAllowlistIds(`${A}\n${A.toUpperCase()}`).ids).toHaveLength(1);
  });

  it("preserves input order", () => {
    expect(parseAllowlistIds(`${B} ${A}`).ids).toEqual([B, A]);
  });
});

describe("invalid entries are surfaced, not dropped", () => {
  it("flags a token that is not a UUID", () => {
    const { ids, invalid } = parseAllowlistIds(`${A}\nnot-an-id`);
    // Still saved — the backend is the authority on what matches, and silently
    // discarding an operator's input is how you get "I added them and nothing
    // happened".
    expect(ids).toEqual([A, "not-an-id"]);
    expect(invalid).toEqual(["not-an-id"]);
  });

  it("reports nothing invalid for a clean list", () => {
    expect(parseAllowlistIds(`${A}, ${B}`).invalid).toEqual([]);
  });

  it("flags a truncated UUID", () => {
    // The realistic typo: a paste that clipped the last group.
    const truncated = A.slice(0, -4);
    expect(parseAllowlistIds(truncated).invalid).toEqual([truncated]);
  });

  it("flags a driver record ID pasted where a user ID belongs", () => {
    // Both are UUIDs, so this cannot be caught by shape — the field's label
    // does that work. Included to state the limit: the check catches junk, not
    // the wrong-but-well-formed ID.
    expect(parseAllowlistIds(B).invalid).toEqual([]);
  });
});

describe("isLikelyUuid", () => {
  it.each([
    [A, true],
    [`  ${A}  `, true],
    [A.toUpperCase(), true],
    ["not-an-id", false],
    ["", false],
    ["12345", false],
  ])("%s -> %s", (value, expected) => {
    expect(isLikelyUuid(value)).toBe(expected);
  });
});
