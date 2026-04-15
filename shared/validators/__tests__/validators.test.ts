/**
 * Tests for shared validators. Keep these fast and pure - no network, no IO.
 */
import {
  validatePhone,
  normalizePhone,
  validateEmail,
  validateLatitude,
  validateLongitude,
  validateCoordinates,
  digitsOnly,
} from "../index";

describe("validatePhone", () => {
  it("accepts 10-digit North American numbers", () => {
    expect(validatePhone("3065551234").valid).toBe(true);
    expect(validatePhone("(306) 555-1234").valid).toBe(true);
    expect(validatePhone("306-555-1234").valid).toBe(true);
  });

  it("accepts E.164 format", () => {
    expect(validatePhone("+13065551234").valid).toBe(true);
  });

  it("rejects too-short numbers", () => {
    const result = validatePhone("555");
    expect(result.valid).toBe(false);
    if (!result.valid) expect(result.reason).toMatch(/at least 10/);
  });

  it("rejects empty input", () => {
    expect(validatePhone("").valid).toBe(false);
  });

  it("rejects overly long numbers", () => {
    expect(validatePhone("1234567890123456").valid).toBe(false);
  });
});

describe("normalizePhone", () => {
  it("prepends +1 to 10-digit numbers", () => {
    expect(normalizePhone("3065551234")).toBe("+13065551234");
    expect(normalizePhone("(306) 555-1234")).toBe("+13065551234");
  });

  it("keeps country code if present", () => {
    expect(normalizePhone("13065551234")).toBe("+13065551234");
  });

  it("returns null for invalid input", () => {
    expect(normalizePhone("555")).toBeNull();
  });
});

describe("validateEmail", () => {
  it("accepts well-formed addresses", () => {
    expect(validateEmail("user@example.com").valid).toBe(true);
    expect(validateEmail("a.b+c@example.co.uk").valid).toBe(true);
  });

  it("rejects the legacy-accepted a@b.c", () => {
    // The old regex /^[^\s@]+@[^\s@]+\.[^\s@]+$/ accepted this.
    // The new regex requires TLD of 2+ chars.
    expect(validateEmail("a@b.c").valid).toBe(false);
  });

  it("rejects empty and whitespace", () => {
    expect(validateEmail("").valid).toBe(false);
    expect(validateEmail("   ").valid).toBe(false);
  });

  it("rejects malformed strings", () => {
    expect(validateEmail("not-an-email").valid).toBe(false);
    expect(validateEmail("@missing-local.com").valid).toBe(false);
    expect(validateEmail("missing-at.com").valid).toBe(false);
  });
});

describe("validateLatitude", () => {
  it("accepts valid range", () => {
    expect(validateLatitude(0).valid).toBe(true);
    expect(validateLatitude(-90).valid).toBe(true);
    expect(validateLatitude(90).valid).toBe(true);
    expect(validateLatitude(52.1332).valid).toBe(true);
  });

  it("rejects out-of-range", () => {
    expect(validateLatitude(91).valid).toBe(false);
    expect(validateLatitude(-90.1).valid).toBe(false);
  });

  it("rejects NaN and Infinity", () => {
    expect(validateLatitude(NaN).valid).toBe(false);
    expect(validateLatitude(Infinity).valid).toBe(false);
    expect(validateLatitude("not a number").valid).toBe(false);
  });

  it("coerces string numbers", () => {
    expect(validateLatitude("52.1").valid).toBe(true);
  });
});

describe("validateLongitude", () => {
  it("accepts valid range", () => {
    expect(validateLongitude(0).valid).toBe(true);
    expect(validateLongitude(-180).valid).toBe(true);
    expect(validateLongitude(180).valid).toBe(true);
  });

  it("rejects out-of-range", () => {
    expect(validateLongitude(181).valid).toBe(false);
    expect(validateLongitude(-180.1).valid).toBe(false);
  });
});

describe("validateCoordinates", () => {
  it("validates both lat and lng", () => {
    expect(validateCoordinates(52.1, -106.6).valid).toBe(true);
    expect(validateCoordinates(91, -106.6).valid).toBe(false);
    expect(validateCoordinates(52.1, 181).valid).toBe(false);
  });
});

describe("digitsOnly", () => {
  it("strips non-digits", () => {
    expect(digitsOnly("(306) 555-1234")).toBe("3065551234");
    expect(digitsOnly("+1 306 555 1234")).toBe("13065551234");
    expect(digitsOnly("")).toBe("");
  });
});
