import { describe, it, expect } from "vitest";
import { getPromotionFormError, type PromotionFormInput } from "../promotionFormSchema";

function baseForm(overrides: Partial<PromotionFormInput> = {}): PromotionFormInput {
  return {
    code: "SAVE10",
    free_ride: false,
    discount_value: "10",
    discount_type: "flat",
    max_discount: "",
    max_uses: "100",
    expiry_date: "",
    ...overrides,
  };
}

describe("getPromotionFormError", () => {
  it("accepts a valid flat-discount form", () => {
    expect(getPromotionFormError(baseForm())).toBeNull();
  });

  it("accepts a valid percentage-discount form", () => {
    expect(getPromotionFormError(baseForm({ discount_type: "percentage", discount_value: "20" }))).toBeNull();
  });

  it("accepts a free-ride form with no discount_value", () => {
    expect(getPromotionFormError(baseForm({ free_ride: true, discount_value: "" }))).toBeNull();
  });

  it("accepts a valid future expiry date", () => {
    const future = new Date(Date.now() + 86400000).toISOString().split("T")[0];
    expect(getPromotionFormError(baseForm({ expiry_date: future }))).toBeNull();
  });

  it("accepts a max_discount within the $500 cap", () => {
    expect(getPromotionFormError(baseForm({ max_discount: "50" }))).toBeNull();
  });

  it("rejects an empty code", () => {
    expect(getPromotionFormError(baseForm({ code: "  " }))).toEqual({
      title: "Missing required fields",
      description: "Please fill in the code.",
    });
  });

  it("rejects a missing discount value when not free_ride", () => {
    expect(getPromotionFormError(baseForm({ discount_value: "" }))).toEqual({
      title: "Missing required fields",
      description: "Please fill in code and discount value.",
    });
  });

  it("rejects a non-numeric discount value", () => {
    expect(getPromotionFormError(baseForm({ discount_value: "abc" }))).toEqual({
      title: "Invalid discount value",
      description: "Discount must be greater than zero.",
    });
  });

  it("rejects a zero discount value", () => {
    expect(getPromotionFormError(baseForm({ discount_value: "0" }))).toEqual({
      title: "Invalid discount value",
      description: "Discount must be greater than zero.",
    });
  });

  it("rejects a percentage discount over 100", () => {
    expect(getPromotionFormError(baseForm({ discount_type: "percentage", discount_value: "150" }))).toEqual({
      title: "Invalid percentage",
      description: "Percentage discount cannot exceed 100%.",
    });
  });

  it("accepts a percentage discount of exactly 100", () => {
    expect(getPromotionFormError(baseForm({ discount_type: "percentage", discount_value: "100" }))).toBeNull();
  });

  it("rejects a flat discount over $500", () => {
    expect(getPromotionFormError(baseForm({ discount_value: "501" }))).toEqual({
      title: "Discount too large",
      description: "Flat discount cannot exceed $500.",
    });
  });

  it("accepts a flat discount of exactly $500", () => {
    expect(getPromotionFormError(baseForm({ discount_value: "500" }))).toBeNull();
  });

  it("rejects a non-numeric max_discount", () => {
    expect(getPromotionFormError(baseForm({ max_discount: "abc" }))).toEqual({
      title: "Invalid max discount cap",
      description: "Max discount cap must be greater than zero.",
    });
  });

  it("rejects a zero max_discount", () => {
    expect(getPromotionFormError(baseForm({ max_discount: "0" }))).toEqual({
      title: "Invalid max discount cap",
      description: "Max discount cap must be greater than zero.",
    });
  });

  it("rejects a max_discount over $500", () => {
    expect(getPromotionFormError(baseForm({ max_discount: "501" }))).toEqual({
      title: "Max discount cap too large",
      description: "Max discount cap cannot exceed $500.",
    });
  });

  it("rejects a non-numeric max_uses", () => {
    expect(getPromotionFormError(baseForm({ max_uses: "abc" }))).toEqual({
      title: "Invalid max uses",
      description: "Max uses must be at least 1.",
    });
  });

  it("rejects a max_uses less than 1", () => {
    expect(getPromotionFormError(baseForm({ max_uses: "0" }))).toEqual({
      title: "Invalid max uses",
      description: "Max uses must be at least 1.",
    });
  });

  it("accepts a max_uses of exactly 1", () => {
    expect(getPromotionFormError(baseForm({ max_uses: "1" }))).toBeNull();
  });

  it("rejects an expiry date in the past", () => {
    const past = new Date(Date.now() - 86400000).toISOString().split("T")[0];
    expect(getPromotionFormError(baseForm({ expiry_date: past }))).toEqual({
      title: "Invalid expiry date",
      description: "Expiry date must be in the future.",
    });
  });

  it("skips discount-tier checks entirely for a free_ride promo, even with an invalid max_uses", () => {
    // free_ride form still runs the max_uses/expiry checks below the discount branch
    expect(getPromotionFormError(baseForm({ free_ride: true, discount_value: "", max_uses: "0" }))).toEqual({
      title: "Invalid max uses",
      description: "Max uses must be at least 1.",
    });
  });
});
