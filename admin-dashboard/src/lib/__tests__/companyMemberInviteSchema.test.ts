import { describe, it, expect } from "vitest";
import { isInviteEmailProvided, isInviteEmailFormatValid } from "../companyMemberInviteSchema";

describe("isInviteEmailProvided", () => {
  it("accepts a non-empty string", () => {
    expect(isInviteEmailProvided("rider@example.com")).toBe(true);
  });

  it("rejects an empty string", () => {
    expect(isInviteEmailProvided("")).toBe(false);
  });

  it("treats a whitespace-only string as provided (matches the original's untrimmed truthiness check)", () => {
    expect(isInviteEmailProvided("   ")).toBe(true);
  });
});

describe("isInviteEmailFormatValid", () => {
  it("accepts a well-formed email", () => {
    expect(isInviteEmailFormatValid("rider@example.com")).toBe(true);
  });

  it("accepts an email with surrounding whitespace (trimmed before matching)", () => {
    expect(isInviteEmailFormatValid("  rider@example.com  ")).toBe(true);
  });

  it("accepts a subdomain email", () => {
    expect(isInviteEmailFormatValid("rider@mail.example.com")).toBe(true);
  });

  it("rejects a string with no @", () => {
    expect(isInviteEmailFormatValid("rider.example.com")).toBe(false);
  });

  it("rejects a string with no domain dot", () => {
    expect(isInviteEmailFormatValid("rider@example")).toBe(false);
  });

  it("rejects a string with a space", () => {
    expect(isInviteEmailFormatValid("rider @example.com")).toBe(false);
  });

  it("rejects an empty string", () => {
    expect(isInviteEmailFormatValid("")).toBe(false);
  });

  it("rejects two @ signs", () => {
    expect(isInviteEmailFormatValid("rider@@example.com")).toBe(false);
  });
});
