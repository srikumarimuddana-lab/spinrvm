"use client";

/**
 * Evidence photos attached to a safety incident (backend migration 340).
 *
 * Extracted from the incident detail drawer rather than inlined so it can be
 * unit-tested: the drawer itself is a private component inside a ~900-line
 * page, and the failure mode this UI exists to prevent — evidence silently not
 * being shown — is exactly the kind of thing that needs a test.
 *
 * Photos come back with a short-lived signed `url` minted per request. The
 * backend stores only the storage key, so these expire; they are never cached
 * or persisted client-side.
 */

import { useState } from "react";
import { AlertTriangle, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import type { SafetyIncidentPhoto } from "@/lib/api";

export function IncidentEvidencePhotos({
    photos,
    formatDateTime,
}: {
    photos: SafetyIncidentPhoto[];
    /** Injected so this component doesn't reach into the page's date helpers. */
    formatDateTime?: (iso: string) => string;
}) {
    const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);

    // Only photos that actually have a URL are navigable in the lightbox;
    // the unsignable ones still get a tile in the grid below.
    const viewable = photos.filter((p) => p.url);

    if (photos.length === 0) return null;

    const current = lightboxIndex !== null ? viewable[lightboxIndex] : null;

    return (
        <section>
            <Label className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">
                Evidence photos ({photos.length})
            </Label>

            <div className="mt-2 grid grid-cols-4 gap-2">
                {photos.map((photo) => {
                    // A photo whose signed URL failed to mint still gets a
                    // tile. Dropping it would tell the reviewer no evidence
                    // exists when it does — the same silent-loss failure this
                    // whole feature was built to fix.
                    if (!photo.url) {
                        return (
                            <div
                                key={photo.id}
                                className="aspect-square rounded-lg border border-dashed border-warning/60 bg-warning/10 flex flex-col items-center justify-center gap-1 p-2 text-center"
                                title="This photo exists but could not be loaded. Retry, or check storage."
                            >
                                <AlertTriangle className="h-4 w-4 text-warning" />
                                <span className="text-[9px] leading-tight text-warning">
                                    Preview unavailable
                                </span>
                            </div>
                        );
                    }
                    const idx = viewable.findIndex((p) => p.id === photo.id);
                    return (
                        <button
                            key={photo.id}
                            type="button"
                            onClick={() => setLightboxIndex(idx)}
                            className="aspect-square rounded-lg overflow-hidden border border-border/60 hover:border-primary focus:outline-none focus:ring-2 focus:ring-primary transition-colors"
                            aria-label={`Open evidence photo ${idx + 1} of ${viewable.length}`}
                        >
                            {/* eslint-disable-next-line @next/next/no-img-element -- expiring
                                signed Supabase URL; next/image cannot optimize it, and plain
                                <img> is the established convention on these admin screens. */}
                            <img
                                src={photo.url}
                                alt={`Evidence ${idx + 1}`}
                                className="w-full h-full object-cover"
                                loading="lazy"
                            />
                        </button>
                    );
                })}
            </div>

            <p className="text-[10px] text-muted-foreground mt-1.5">
                Links expire after 1 hour — reopen the incident to refresh.
            </p>

            {/* Dialog gives focus-trap + Esc-to-close for free. Evidence is
                often a small detail (a plate, a name on a receipt) that a
                thumbnail hides, so full size matters. */}
            <Dialog
                open={lightboxIndex !== null}
                onOpenChange={(open) => !open && setLightboxIndex(null)}
            >
                <DialogContent className="max-w-4xl">
                    <DialogHeader>
                        <DialogTitle className="text-sm">
                            Evidence photo {(lightboxIndex ?? 0) + 1} of {viewable.length}
                        </DialogTitle>
                    </DialogHeader>

                    {current?.url && (
                        <div className="space-y-3">
                            <div className="bg-muted/30 rounded-lg overflow-hidden flex items-center justify-center max-h-[70vh]">
                                {/* object-contain, never cover: cropping evidence could
                                    hide the very detail being reviewed. */}
                                {/* eslint-disable-next-line @next/next/no-img-element -- see above */}
                                <img
                                    src={current.url}
                                    alt={`Evidence ${(lightboxIndex ?? 0) + 1}`}
                                    className="max-w-full max-h-[70vh] object-contain"
                                />
                            </div>
                            <div className="flex items-center justify-between gap-2">
                                <span className="text-[11px] text-muted-foreground">
                                    {current.created_at && formatDateTime
                                        ? `Attached ${formatDateTime(current.created_at)}`
                                        : ""}
                                </span>
                                <div className="flex items-center gap-2">
                                    <a
                                        href={current.url}
                                        target="_blank"
                                        rel="noreferrer"
                                        className="text-[11px] text-primary hover:underline inline-flex items-center gap-1"
                                    >
                                        Open full size
                                        <ExternalLink className="h-3 w-3" />
                                    </a>
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        disabled={(lightboxIndex ?? 0) === 0}
                                        onClick={() => setLightboxIndex((lightboxIndex ?? 0) - 1)}
                                    >
                                        Previous
                                    </Button>
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        disabled={(lightboxIndex ?? 0) >= viewable.length - 1}
                                        onClick={() => setLightboxIndex((lightboxIndex ?? 0) + 1)}
                                    >
                                        Next
                                    </Button>
                                </div>
                            </div>
                        </div>
                    )}
                </DialogContent>
            </Dialog>
        </section>
    );
}
