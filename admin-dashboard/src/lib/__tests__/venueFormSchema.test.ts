import { describe, it, expect } from "vitest";
import { isVenueNameValid } from "../venueFormSchema";

describe("isVenueNameValid", () => {
  it("accepts a non-empty name", () => {
    expect(isVenueNameValid("Cornwall Centre")).toBe(true);
  });

  it("rejects an empty name", () => {
    expect(isVenueNameValid("")).toBe(false);
  });

  it("rejects a whitespace-only name", () => {
    expect(isVenueNameValid("   ")).toBe(false);
  });

  it("accepts a name with surrounding whitespace (trimmed non-empty)", () => {
    expect(isVenueNameValid("  Midtown Plaza  ")).toBe(true);
  });
});
