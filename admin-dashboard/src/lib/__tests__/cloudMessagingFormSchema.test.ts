import { describe, it, expect } from "vitest";
import { getBroadcastFormError, getSuppressionFormError, type BroadcastFormInput } from "../cloudMessagingFormSchema";

function baseForm(overrides: Partial<BroadcastFormInput> = {}): BroadcastFormInput {
  return {
    title: "Service update",
    description: "We've improved the app.",
    audience: "customers",
    particularIds: [],
    isScheduled: false,
    scheduledAt: "",
    sendPush: true,
    sendEmail: false,
    sendSms: false,
    ...overrides,
  };
}

describe("getBroadcastFormError", () => {
  it("accepts a valid broadcast-to-all form", () => {
    expect(getBroadcastFormError(baseForm())).toBeNull();
  });

  it("accepts a valid particular-audience form with recipients selected", () => {
    expect(
      getBroadcastFormError(baseForm({ audience: "particular_customer", particularIds: ["u1"] })),
    ).toBeNull();
  });

  it("accepts a valid scheduled form with a schedule time set", () => {
    expect(getBroadcastFormError(baseForm({ isScheduled: true, scheduledAt: "2027-01-01T10:00" }))).toBeNull();
  });

  it("accepts multiple delivery channels selected", () => {
    expect(getBroadcastFormError(baseForm({ sendPush: true, sendEmail: true, sendSms: true }))).toBeNull();
  });

  it("rejects an empty title", () => {
    expect(getBroadcastFormError(baseForm({ title: "" }))).toEqual({
      title: "Missing fields",
      description: "Please fill in title and description.",
    });
  });

  it("rejects a whitespace-only title", () => {
    expect(getBroadcastFormError(baseForm({ title: "   " }))).toEqual({
      title: "Missing fields",
      description: "Please fill in title and description.",
    });
  });

  it("rejects an empty description", () => {
    expect(getBroadcastFormError(baseForm({ description: "" }))).toEqual({
      title: "Missing fields",
      description: "Please fill in title and description.",
    });
  });

  it("rejects a particular_customer audience with no recipients", () => {
    expect(getBroadcastFormError(baseForm({ audience: "particular_customer", particularIds: [] }))).toEqual({
      title: "No recipients selected",
      description: "Please select at least one user/driver.",
    });
  });

  it("rejects a particular_driver audience with no recipients", () => {
    expect(getBroadcastFormError(baseForm({ audience: "particular_driver", particularIds: [] }))).toEqual({
      title: "No recipients selected",
      description: "Please select at least one user/driver.",
    });
  });

  it("does not require recipients for a non-particular audience", () => {
    expect(getBroadcastFormError(baseForm({ audience: "drivers", particularIds: [] }))).toBeNull();
  });

  it("rejects a scheduled send with no schedule time", () => {
    expect(getBroadcastFormError(baseForm({ isScheduled: true, scheduledAt: "" }))).toEqual({
      title: "Missing schedule time",
      description: "Please select a date and time.",
    });
  });

  it("rejects no delivery channel selected", () => {
    expect(
      getBroadcastFormError(baseForm({ sendPush: false, sendEmail: false, sendSms: false })),
    ).toEqual({
      title: "No delivery channel",
      description: "Please select at least one delivery channel.",
    });
  });

  it("checks title/description before recipients (first-error-wins order)", () => {
    expect(
      getBroadcastFormError(baseForm({ title: "", audience: "particular_customer", particularIds: [] })),
    ).toEqual({ title: "Missing fields", description: "Please fill in title and description." });
  });

  it("checks recipients before schedule time", () => {
    expect(
      getBroadcastFormError(
        baseForm({ audience: "particular_customer", particularIds: [], isScheduled: true, scheduledAt: "" }),
      ),
    ).toEqual({ title: "No recipients selected", description: "Please select at least one user/driver." });
  });

  it("checks schedule time before delivery channel", () => {
    expect(
      getBroadcastFormError(
        baseForm({ isScheduled: true, scheduledAt: "", sendPush: false, sendEmail: false, sendSms: false }),
      ),
    ).toEqual({ title: "Missing schedule time", description: "Please select a date and time." });
  });
});

describe("getSuppressionFormError", () => {
  it("accepts a valid target", () => {
    expect(getSuppressionFormError("rider@example.com")).toBeNull();
  });

  it("rejects an empty target", () => {
    expect(getSuppressionFormError("")).toEqual({
      title: "Missing value",
      description: "Enter an email or phone to suppress.",
    });
  });

  it("rejects a whitespace-only target", () => {
    expect(getSuppressionFormError("   ")).toEqual({
      title: "Missing value",
      description: "Enter an email or phone to suppress.",
    });
  });
});
