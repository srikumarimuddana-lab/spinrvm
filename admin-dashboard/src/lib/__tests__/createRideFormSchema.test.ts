import { describe, it, expect } from "vitest";
import { getCreateRideFormError } from "../createRideFormSchema";

const rider = { id: "r1" };
const pickup = { lat: 1, lng: 1, address: "A" };
const dropoff = { lat: 2, lng: 2, address: "B" };

function baseForm(overrides: Partial<{ rider: unknown; pickup: unknown; dropoff: unknown; finalFare: string }> = {}) {
  return {
    rider,
    pickup,
    dropoff,
    finalFare: "12.50",
    ...overrides,
  };
}

describe("getCreateRideFormError", () => {
  it("accepts a fully valid form", () => {
    expect(getCreateRideFormError(baseForm())).toBeNull();
  });

  it("accepts an empty finalFare (defaults to 0)", () => {
    expect(getCreateRideFormError(baseForm({ finalFare: "" }))).toBeNull();
  });

  it("accepts a zero finalFare", () => {
    expect(getCreateRideFormError(baseForm({ finalFare: "0" }))).toBeNull();
  });

  it("rejects a missing rider", () => {
    expect(getCreateRideFormError(baseForm({ rider: null }))).toBe("Please select a rider.");
  });

  it("rejects a missing pickup", () => {
    expect(getCreateRideFormError(baseForm({ pickup: null }))).toBe(
      "Please select a valid pickup location from the suggestions.",
    );
  });

  it("rejects a missing dropoff", () => {
    expect(getCreateRideFormError(baseForm({ dropoff: null }))).toBe(
      "Please select a valid dropoff location from the suggestions.",
    );
  });

  it("rejects a negative finalFare", () => {
    expect(getCreateRideFormError(baseForm({ finalFare: "-5" }))).toBe("Total fare must be a non-negative number.");
  });

  it("rejects a non-numeric finalFare", () => {
    expect(getCreateRideFormError(baseForm({ finalFare: "abc" }))).toBe("Total fare must be a non-negative number.");
  });

  it("checks rider before pickup/dropoff/fare (first-error-wins order)", () => {
    expect(getCreateRideFormError(baseForm({ rider: null, pickup: null, finalFare: "-1" }))).toBe(
      "Please select a rider.",
    );
  });

  it("checks pickup before dropoff/fare", () => {
    expect(getCreateRideFormError(baseForm({ pickup: null, dropoff: null, finalFare: "-1" }))).toBe(
      "Please select a valid pickup location from the suggestions.",
    );
  });

  it("checks dropoff before fare", () => {
    expect(getCreateRideFormError(baseForm({ dropoff: null, finalFare: "-1" }))).toBe(
      "Please select a valid dropoff location from the suggestions.",
    );
  });
});
