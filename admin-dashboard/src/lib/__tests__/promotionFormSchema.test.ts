import {
  isPromoCodeValid,
  isDiscountValuePresent,
  isDiscountValueValid,
  isPercentageDiscountValid,
  isFlatDiscountValid,
  isMaxDiscountValid,
  isMaxDiscountWithinLimit,
  isMaxUsesValid,
  isExpiryDateValid,
  getPromotionFormError,
  type PromotionFormField,
} from "../promotionFormSchema";

const baseForm: PromotionFormField = {
  code: "SAVE10",
  freeRide: false,
  discountType: "percentage",
  discountValue: "10",
  maxDiscount: "",
  maxUses: "100",
  expiryDate: "",
};

describe("isPromoCodeValid", () => {
  it("rejects an empty/whitespace-only code", () => {
    expect(isPromoCodeValid("")).toBe(false);
    expect(isPromoCodeValid("   ")).toBe(false);
  });
  it("accepts a non-empty code", () => {
    expect(isPromoCodeValid("SAVE10")).toBe(true);
  });
});

describe("isDiscountValuePresent / isDiscountValueValid", () => {
  it("rejects an empty discount value", () => {
    expect(isDiscountValuePresent("")).toBe(false);
  });
  it("rejects a zero or negative discount value", () => {
    expect(isDiscountValueValid("0")).toBe(false);
    expect(isDiscountValueValid("-5")).toBe(false);
  });
  it("accepts a positive discount value", () => {
    expect(isDiscountValueValid("10")).toBe(true);
  });
});

describe("isPercentageDiscountValid / isFlatDiscountValid", () => {
  it("rejects a percentage discount over 100", () => {
    expect(isPercentageDiscountValid("percentage", "150")).toBe(false);
  });
  it("accepts a percentage discount at or under 100", () => {
    expect(isPercentageDiscountValid("percentage", "100")).toBe(true);
  });
  it("does not apply the percentage cap to a flat discount", () => {
    expect(isPercentageDiscountValid("flat", "150")).toBe(true);
  });
  it("rejects a flat discount over $500", () => {
    expect(isFlatDiscountValid("flat", "600")).toBe(false);
  });
  it("accepts a flat discount at or under $500", () => {
    expect(isFlatDiscountValid("flat", "500")).toBe(true);
  });
  it("does not apply the flat cap to a percentage discount", () => {
    expect(isFlatDiscountValid("percentage", "600")).toBe(true);
  });
});

describe("isMaxDiscountValid / isMaxDiscountWithinLimit", () => {
  it("treats an empty max-discount cap as valid (optional field)", () => {
    expect(isMaxDiscountValid("")).toBe(true);
    expect(isMaxDiscountWithinLimit("")).toBe(true);
  });
  it("rejects a zero or negative max-discount cap when set", () => {
    expect(isMaxDiscountValid("0")).toBe(false);
    expect(isMaxDiscountValid("-5")).toBe(false);
  });
  it("rejects a max-discount cap over $500", () => {
    expect(isMaxDiscountWithinLimit("600")).toBe(false);
  });
  it("accepts a valid max-discount cap within range", () => {
    expect(isMaxDiscountValid("50")).toBe(true);
    expect(isMaxDiscountWithinLimit("50")).toBe(true);
  });
});

describe("isMaxUsesValid", () => {
  it("rejects a non-numeric or sub-1 max-uses value", () => {
    expect(isMaxUsesValid("abc")).toBe(false);
    expect(isMaxUsesValid("0")).toBe(false);
  });
  it("accepts a max-uses value of at least 1", () => {
    expect(isMaxUsesValid("1")).toBe(true);
    expect(isMaxUsesValid("100")).toBe(true);
  });
});

describe("isExpiryDateValid", () => {
  it("treats an empty expiry date as valid (optional field)", () => {
    expect(isExpiryDateValid("")).toBe(true);
  });
  it("rejects a past expiry date", () => {
    expect(isExpiryDateValid("2020-01-01")).toBe(false);
  });
  it("accepts a future expiry date", () => {
    const future = new Date();
    future.setFullYear(future.getFullYear() + 1);
    expect(isExpiryDateValid(future.toISOString().split("T")[0])).toBe(true);
  });
});

describe("getPromotionFormError", () => {
  it("returns the code error first when the code is missing", () => {
    expect(getPromotionFormError({ ...baseForm, code: "" })).toEqual({
      title: "Missing required fields",
      description: "Please fill in the code.",
    });
  });

  it("skips all discount checks for a free-ride promo", () => {
    expect(
      getPromotionFormError({ ...baseForm, freeRide: true, discountValue: "", maxDiscount: "-5" }),
    ).toBeNull();
  });

  it("returns the discount-value-missing error for a non-free-ride promo", () => {
    expect(getPromotionFormError({ ...baseForm, discountValue: "" })).toEqual({
      title: "Missing required fields",
      description: "Please fill in code and discount value.",
    });
  });

  it("returns the invalid-discount-value error", () => {
    expect(getPromotionFormError({ ...baseForm, discountValue: "0" })).toEqual({
      title: "Invalid discount value",
      description: "Discount must be greater than zero.",
    });
  });

  it("returns the percentage-cap error", () => {
    expect(getPromotionFormError({ ...baseForm, discountValue: "150" })).toEqual({
      title: "Invalid percentage",
      description: "Percentage discount cannot exceed 100%.",
    });
  });

  it("returns the flat-cap error", () => {
    expect(
      getPromotionFormError({ ...baseForm, discountType: "flat", discountValue: "600" }),
    ).toEqual({
      title: "Discount too large",
      description: "Flat discount cannot exceed $500.",
    });
  });

  it("returns the max-discount-cap-invalid error", () => {
    expect(getPromotionFormError({ ...baseForm, maxDiscount: "0" })).toEqual({
      title: "Invalid max discount cap",
      description: "Max discount cap must be greater than zero.",
    });
  });

  it("returns the max-discount-cap-too-large error", () => {
    expect(getPromotionFormError({ ...baseForm, maxDiscount: "600" })).toEqual({
      title: "Max discount cap too large",
      description: "Max discount cap cannot exceed $500.",
    });
  });

  it("returns the max-uses error", () => {
    expect(getPromotionFormError({ ...baseForm, maxUses: "0" })).toEqual({
      title: "Invalid max uses",
      description: "Max uses must be at least 1.",
    });
  });

  it("returns the expiry-date error", () => {
    expect(getPromotionFormError({ ...baseForm, expiryDate: "2020-01-01" })).toEqual({
      title: "Invalid expiry date",
      description: "Expiry date must be in the future.",
    });
  });

  it("returns null when every check passes", () => {
    expect(getPromotionFormError(baseForm)).toBeNull();
  });
});
