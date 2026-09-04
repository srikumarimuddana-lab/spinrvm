import { describe, it, expect } from "vitest";
import { isTicketSubjectValid } from "../createTicketFormSchema";

describe("isTicketSubjectValid", () => {
  it("accepts a non-empty subject", () => {
    expect(isTicketSubjectValid("App crashes on login")).toBe(true);
  });

  it("accepts a subject with leading/trailing whitespace around content", () => {
    expect(isTicketSubjectValid("  Refund request  ")).toBe(true);
  });

  it("accepts a single-character subject", () => {
    expect(isTicketSubjectValid("x")).toBe(true);
  });

  it("rejects an empty subject", () => {
    expect(isTicketSubjectValid("")).toBe(false);
  });

  it("rejects a whitespace-only subject", () => {
    expect(isTicketSubjectValid("   ")).toBe(false);
  });

  it("rejects a tab/newline-only subject", () => {
    expect(isTicketSubjectValid("\t\n")).toBe(false);
  });
});
