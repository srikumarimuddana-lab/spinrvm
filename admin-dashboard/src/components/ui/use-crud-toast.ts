/**
 * CRUD toast helper — consistent success/error toasts for admin operations.
 *
 * Usage:
 *   const crudToast = useCrudToast();
 *
 *   try {
 *     await createServiceArea(...);
 *     crudToast.created("Service area");
 *   } catch (e) {
 *     crudToast.error("create service area", e);
 *   }
 */

import { useToast } from "./use-toast";

export function useCrudToast() {
  const { toast } = useToast();

  return {
    /** Show success toast after a CREATE. */
    created: (entity: string, description?: string) =>
      toast({
        title: `${entity} created`,
        description: description ?? `${entity} added successfully.`,
      }),

    /** Show success toast after an UPDATE. */
    updated: (entity: string, description?: string) =>
      toast({
        title: `${entity} updated`,
        description: description ?? `${entity} saved.`,
      }),

    /** Show success toast after a DELETE. */
    deleted: (entity: string, description?: string) =>
      toast({
        title: `${entity} deleted`,
        description: description ?? `${entity} removed.`,
      }),

    /** Show generic success toast. */
    success: (title: string, description?: string) =>
      toast({ title, description }),

    /** Show error toast. `verb` describes the failed action (e.g. "create user"). */
    error: (verb: string, error: unknown) => {
      const message = extractErrorMessage(error);
      toast({
        title: `Failed to ${verb}`,
        description: message,
        variant: "destructive",
      });
    },

    /** Show a generic destructive toast (e.g. validation error). */
    warn: (title: string, description?: string) =>
      toast({ title, description, variant: "destructive" }),
  };
}

function extractErrorMessage(e: unknown): string | undefined {
  if (!e) return undefined;
  if (typeof e === "string") return e;
  if (e instanceof Error) return e.message;
  if (typeof e === "object" && e !== null) {
    const anyE = e as { message?: string; detail?: string };
    return anyE.message ?? anyE.detail;
  }
  return undefined;
}
