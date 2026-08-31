import {
  isRefundAmountValid,
  isRefundWithinOriginalFare,
  getPartialRefundError,
} from "../disputeResolutionSchema";

describe("isRefundAmountValid", () => {
  it("returns false for a non-numeric string", () => {
    expect(isRefundAmountValid("abc")).toBe(false);
    expect(isRefundAmountValid("")).toBe(false);
  });

  it("returns false for zero or negative amounts", () => {
    expect(isRefundAmountValid("0")).toBe(false);
    expect(isRefundAmountValid("-5")).toBe(false);
  });

  it("returns true for a positive numeric amount", () => {
    expect(isRefundAmountValid("15.50")).toBe(true);
  });
});

describe("isRefundWithinOriginalFare", () => {
  it("returns false when the refund exceeds the original fare", () => {
    expect(isRefundWithinOriginalFare("30", 25)).toBe(false);
  });

  it("returns true when the refund is within the original fare", () => {
    expect(isRefundWithinOriginalFare("20", 25)).toBe(true);
  });

  it("returns true when the refund exactly equals the original fare", () => {
    expect(isRefundWithinOriginalFare("25", 25)).toBe(true);
  });
});

describe("getPartialRefundError", () => {
  it("returns the amount error first when the amount is invalid", () => {
    expect(getPartialRefundError("0", 25)).toBe("Refund amount must be greater than zero");
  });

  it("returns the fare-cap error, with the dollar figure, when the amount exceeds the original fare", () => {
    expect(getPartialRefundError("30", 25)).toBe("Refund cannot exceed the original fare of $25.00");
  });

  it("returns null when the refund amount is valid and within the original fare", () => {
    expect(getPartialRefundError("20", 25)).toBeNull();
  });
});
