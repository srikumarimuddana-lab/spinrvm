import { describe, it, expect } from "vitest";
import { isPhotoFileTypeValid } from "../driverPhotoUploadSchema";

describe("isPhotoFileTypeValid", () => {
  it("accepts image/jpeg", () => {
    expect(isPhotoFileTypeValid({ type: "image/jpeg" })).toBe(true);
  });

  it("accepts image/png", () => {
    expect(isPhotoFileTypeValid({ type: "image/png" })).toBe(true);
  });

  it("accepts image/webp", () => {
    expect(isPhotoFileTypeValid({ type: "image/webp" })).toBe(true);
  });

  it("accepts image/gif", () => {
    expect(isPhotoFileTypeValid({ type: "image/gif" })).toBe(true);
  });

  it("accepts any image/* MIME type (prefix check, not a strict list)", () => {
    expect(isPhotoFileTypeValid({ type: "image/svg+xml" })).toBe(true);
  });

  it("rejects application/pdf", () => {
    expect(isPhotoFileTypeValid({ type: "application/pdf" })).toBe(false);
  });

  it("rejects text/plain", () => {
    expect(isPhotoFileTypeValid({ type: "text/plain" })).toBe(false);
  });

  it("rejects an empty MIME type", () => {
    expect(isPhotoFileTypeValid({ type: "" })).toBe(false);
  });

  it("rejects video/mp4", () => {
    expect(isPhotoFileTypeValid({ type: "video/mp4" })).toBe(false);
  });
});
