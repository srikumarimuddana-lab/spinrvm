import {
  isWalletAmountValid,
  isWalletReasonValid,
  getWalletActionError,
} from "../userWalletActionSchema";

describe("isWalletAmountValid", () => {
  it("returns false for an empty string", () => {
    expect(isWalletAmountValid("")).toBe(false);
  });

  it("returns false for a zero or negative amount", () => {
    expect(isWalletAmountValid("0")).toBe(false);
    expect(isWalletAmountValid("-5")).toBe(false);
  });

  it("returns false for a value with more than 2 decimal places", () => {
    expect(isWalletAmountValid("5.123")).toBe(false);
  });

  it("returns false for a non-numeric string", () => {
    expect(isWalletAmountValid("abc")).toBe(false);
  });

  it("returns true for a valid positive amount", () => {
    expect(isWalletAmountValid("5")).toBe(true);
    expect(isWalletAmountValid("12.50")).toBe(true);
  });
});

describe("isWalletReasonValid", () => {
  it("returns false for an empty or whitespace-only reason", () => {
    expect(isWalletReasonValid("")).toBe(false);
    expect(isWalletReasonValid("   ")).toBe(false);
  });

  it("returns false for a reason under 3 characters after trimming", () => {
    expect(isWalletReasonValid("ab")).toBe(false);
    expect(isWalletReasonValid(" ab ")).toBe(false);
  });

  it("returns true for a reason at least 3 characters after trimming", () => {
    expect(isWalletReasonValid("abc")).toBe(true);
    expect(isWalletReasonValid("Goodwill credit")).toBe(true);
  });
});

describe("getWalletActionError", () => {
  it("returns the amount error first when both amount and reason are invalid", () => {
    expect(getWalletActionError("", "")).toBe("Enter a positive amount");
  });

  it("returns the reason error when only the amount is valid", () => {
    expect(getWalletActionError("10", "ab")).toBe("Reason must be at least 3 characters");
  });

  it("returns null when both amount and reason are valid", () => {
    expect(getWalletActionError("10", "Goodwill credit")).toBeNull();
  });
});
