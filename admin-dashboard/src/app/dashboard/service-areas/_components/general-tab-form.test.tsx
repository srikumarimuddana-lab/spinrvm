/**
 * UX program W2.1c: saving a surge multiplier above 2.5× without a written
 * justification is still blocked, and the reason now shows as a persistent
 * error toast instead of a browser alert().
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

const toast = vi.fn();
const onSave = vi.fn();

vi.mock("@/components/ui/use-toast", () => ({ useToast: () => ({ toast }) }));
vi.mock("@/lib/api/settings-ai", () => ({ getSettings: () => Promise.resolve({}) }));
vi.mock("./service-area-shared", async (importOriginal) => ({
    ...(await importOriginal<Record<string, unknown>>()),
    GeofenceMap: () => null,
}));

import GeneralTabForm from "./general-tab-form";

describe("GeneralTabForm surge justification", () => {
    beforeEach(() => {
        toast.mockClear();
        onSave.mockReset().mockResolvedValue(undefined);
    });

    it("blocks an above-cap surge save without justification and shows an error toast", () => {
        const { container } = render(
            <GeneralTabForm area={{ id: "sa-1", name: "Regina", surge_enabled: true, surge_multiplier: 1.0 }} onSave={onSave} onDelete={vi.fn()} />,
        );
        const multiplier = container.querySelector('input[type="number"][step="0.1"][min="1"]') as HTMLInputElement;
        fireEvent.change(multiplier, { target: { value: "3" } });
        fireEvent.click(screen.getByRole("button", { name: "Save General Settings" }));

        expect(onSave).not.toHaveBeenCalled();
        expect(toast).toHaveBeenCalledWith(expect.objectContaining({
            title: "Justification required",
            variant: "destructive",
        }));
    });
});
