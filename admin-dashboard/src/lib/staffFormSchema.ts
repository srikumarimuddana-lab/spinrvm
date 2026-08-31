import { z } from "zod";

/**
 * dashboard/staff/page.tsx's `handleSubmit` -- validation extracted from
 * two inline checks per ACTION_ITEMS.md B39. Staff accounts are the admin
 * RBAC surface (module-grant workflow, see
 * backend/routes/admin/staff.py's AVAILABLE_MODULES/ROLE_PRESETS) -- a
 * validation gap here (e.g. an empty email reaching `createStaff`) has
 * real access-control consequence, not just a UX bug.
 *
 * Both predicates below are byte-for-byte equivalent to the checks they
 * replace: plain truthy checks on the raw field, same as the original --
 * no `.trim()` added, since that would be a validation-rule change, not
 * a pure extraction.
 */

export const staffRequiredFieldsSchema = z.object({
  email: z.string().min(1),
  first_name: z.string().min(1),
  last_name: z.string().min(1),
});

/**
 * Mirrors `handleSubmit`'s `!form.email || !form.first_name ||
 * !form.last_name` guard -- the silent early-return shared by both the
 * create and edit paths.
 */
export function isStaffRequiredFieldsValid(
  email: string,
  firstName: string,
  lastName: string,
): boolean {
  return staffRequiredFieldsSchema.safeParse({
    email,
    first_name: firstName,
    last_name: lastName,
  }).success;
}

export const staffPasswordSchema = z.string().min(1);

/**
 * Mirrors `handleSubmit`'s `!form.password` guard -- only checked on the
 * create path (`!editingId`); editing an existing staff member never
 * touches the password field.
 */
export function isStaffPasswordValid(password: string): boolean {
  return staffPasswordSchema.safeParse(password).success;
}
