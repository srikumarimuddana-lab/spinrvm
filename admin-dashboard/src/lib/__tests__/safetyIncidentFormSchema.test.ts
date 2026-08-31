import { describe, it, expect } from "vitest";
import { getLogIncidentFormError, getMergeIncidentError } from "../safetyIncidentFormSchema";

describe("getLogIncidentFormError", () => {
  it("accepts a valid category and description", () => {
    expect(getLogIncidentFormError("unsafe_driving", "Driver ran a red light")).toBeNull();
  });

  it("rejects an empty category", () => {
    expect(getLogIncidentFormError("", "Driver ran a red light")).toBe("Category and description are required");
  });

  it("rejects a whitespace-only category", () => {
    expect(getLogIncidentFormError("   ", "Driver ran a red light")).toBe("Category and description are required");
  });

  it("rejects an empty description", () => {
    expect(getLogIncidentFormError("unsafe_driving", "")).toBe("Category and description are required");
  });

  it("rejects a whitespace-only description", () => {
    expect(getLogIncidentFormError("unsafe_driving", "   ")).toBe("Category and description are required");
  });

  it("rejects both empty", () => {
    expect(getLogIncidentFormError("", "")).toBe("Category and description are required");
  });
});

describe("getMergeIncidentError", () => {
  const incidentId = "incident-abc-123";

  it("accepts a valid, different target ID", () => {
    expect(getMergeIncidentError("incident-xyz-789", incidentId)).toBeNull();
  });

  it("accepts a target ID with surrounding whitespace (trimmed before comparison)", () => {
    expect(getMergeIncidentError("  incident-xyz-789  ", incidentId)).toBeNull();
  });

  it("rejects an empty target ID", () => {
    expect(getMergeIncidentError("", incidentId)).toBe("Enter the canonical incident ID to merge into");
  });

  it("rejects a whitespace-only target ID", () => {
    expect(getMergeIncidentError("   ", incidentId)).toBe("Enter the canonical incident ID to merge into");
  });

  it("rejects merging an incident into itself", () => {
    expect(getMergeIncidentError(incidentId, incidentId)).toBe("Cannot merge an incident into itself");
  });

  it("rejects a self-merge even with surrounding whitespace on the input", () => {
    expect(getMergeIncidentError(`  ${incidentId}  `, incidentId)).toBe("Cannot merge an incident into itself");
  });

  it("checks presence before the self-merge check (first-error-wins order)", () => {
    expect(getMergeIncidentError("", "")).toBe("Enter the canonical incident ID to merge into");
  });
});
