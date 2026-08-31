import { describe, it, expect } from "vitest";
import { isModerationReasonValid } from "../userModerationSchema";

describe("isModerationReasonValid", () => {
  it("accepts a non-empty reason", () => {
    expect(isModerationReasonValid("Repeated policy violations")).toBe(true);
  });

  it("rejects an empty reason", () => {
    expect(isModerationReasonValid("")).toBe(false);
  });

  it("rejects a whitespace-only reason", () => {
    expect(isModerationReasonValid("   ")).toBe(false);
  });

  it("accepts a reason with surrounding whitespace (trimmed non-empty)", () => {
    expect(isModerationReasonValid("  fraud  ")).toBe(true);
  });
});
