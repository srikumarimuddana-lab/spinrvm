import { describe, it, expect } from "vitest";
import { isReplyContentValid, isNoteContentValid } from "../ticketReplyFormSchema";

describe("isReplyContentValid", () => {
  it("accepts non-empty sanitized plain text", () => {
    expect(isReplyContentValid("Thanks for reaching out, here's an update.")).toBe(true);
  });

  it("accepts text with leading/trailing whitespace around content", () => {
    expect(isReplyContentValid("  Looking into this now.  ")).toBe(true);
  });

  it("rejects an empty string", () => {
    expect(isReplyContentValid("")).toBe(false);
  });

  it("rejects a whitespace-only string (e.g. HTML that stripped to nothing but whitespace)", () => {
    expect(isReplyContentValid("   ")).toBe(false);
  });
});

describe("isNoteContentValid", () => {
  it("accepts a non-empty note", () => {
    expect(isNoteContentValid("Escalated to billing team.")).toBe(true);
  });

  it("accepts a note with leading/trailing whitespace around content", () => {
    expect(isNoteContentValid("  Follow up tomorrow.  ")).toBe(true);
  });

  it("rejects an empty note", () => {
    expect(isNoteContentValid("")).toBe(false);
  });

  it("rejects a whitespace-only note", () => {
    expect(isNoteContentValid("   ")).toBe(false);
  });

  it("rejects a tab/newline-only note", () => {
    expect(isNoteContentValid("\t\n")).toBe(false);
  });
});
