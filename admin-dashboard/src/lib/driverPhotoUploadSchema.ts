/**
 * dashboard/drivers/page.tsx's `handlePhotoUpload` -- validation
 * extracted from the inline MIME-type check per ACTION_ITEMS.md B39
 * (broader-sweep candidate: admin-dashboard #8, photo-upload MIME
 * check). Pure extraction, byte-for-byte equivalent of the check it
 * replaces -- no behavior change.
 *
 * Not a zod schema -- a single `File.type` string-prefix predicate has
 * no meaningful shape to validate beyond the one check itself; a zod
 * wrapper would add indirection without adding anything zod's parsing
 * machinery is for. Kept in `lib/` alongside the other B39 extractions
 * for consistency of location and test-coverage discoverability.
 */

/** Mirrors `handlePhotoUpload`'s `!file.type.startsWith("image/")` check (inverted). */
export function isPhotoFileTypeValid(file: Pick<File, "type">): boolean {
  return file.type.startsWith("image/");
}
