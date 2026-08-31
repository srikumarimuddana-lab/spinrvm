import { describe, it, expect } from "vitest";
import { isFaqFormValid } from "../faqFormSchema";

describe("isFaqFormValid", () => {
  it("accepts a valid question and answer", () => {
    expect(isFaqFormValid("How do I book a ride?", "Tap the Book button on the home screen.")).toBe(true);
  });

  it("rejects an empty question", () => {
    expect(isFaqFormValid("", "An answer.")).toBe(false);
  });

  it("rejects a whitespace-only question", () => {
    expect(isFaqFormValid("   ", "An answer.")).toBe(false);
  });

  it("rejects an empty answer", () => {
    expect(isFaqFormValid("A question?", "")).toBe(false);
  });

  it("rejects a whitespace-only answer", () => {
    expect(isFaqFormValid("A question?", "   ")).toBe(false);
  });

  it("rejects both empty", () => {
    expect(isFaqFormValid("", "")).toBe(false);
  });
});
