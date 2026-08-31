import { describe, it, expect } from "vitest";
import { getRideFlagFormError } from "../rideFlagFormSchema";

describe("getRideFlagFormError", () => {
  it("accepts a valid reason", () => {
    expect(getRideFlagFormError("misbehaved")).toBeNull();
  });

  it("rejects an empty reason", () => {
    expect(getRideFlagFormError("")).toEqual({
      title: "Missing reason",
      description: "Please select a reason for the flag.",
    });
  });
});
