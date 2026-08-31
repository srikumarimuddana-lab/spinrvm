import { describe, it, expect } from "vitest";
import { getRideComplaintFormError } from "../rideComplaintFormSchema";

describe("getRideComplaintFormError", () => {
  it("accepts a valid category and description", () => {
    expect(getRideComplaintFormError("safety", "Driver ran a red light")).toBeNull();
  });

  it("rejects an empty category", () => {
    expect(getRideComplaintFormError("", "Driver ran a red light")).toEqual({
      title: "Missing fields",
      description: "Please select a category and describe the issue.",
    });
  });

  it("rejects an empty description", () => {
    expect(getRideComplaintFormError("safety", "")).toEqual({
      title: "Missing fields",
      description: "Please select a category and describe the issue.",
    });
  });

  it("rejects both empty", () => {
    expect(getRideComplaintFormError("", "")).toEqual({
      title: "Missing fields",
      description: "Please select a category and describe the issue.",
    });
  });
});
