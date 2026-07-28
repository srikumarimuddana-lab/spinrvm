"use client";

import { useCallback, useRef, useState } from "react";
import { Upload, FileArchive, X } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Drag-and-drop (+ click-to-browse) ZIP file picker. No dedicated dropzone
 * component exists elsewhere in this codebase — the existing import pages
 * (drivers/import, bulk-operations) all use a plain <input type="file">.
 * This one supports drag-and-drop because the bundle ZIP is a larger,
 * less-routine upload (profile + documents + history) where drag-and-drop
 * meaningfully beats "click, navigate a file picker."
 */
export function BundleDropzone({
    file,
    onFileSelected,
    accept = ".zip,application/zip",
}: {
    file: File | null;
    onFileSelected: (file: File | null) => void;
    accept?: string;
}) {
    const [isDragging, setIsDragging] = useState(false);
    const inputRef = useRef<HTMLInputElement>(null);

    const onDrop = useCallback(
        (e: React.DragEvent<HTMLDivElement>) => {
            e.preventDefault();
            setIsDragging(false);
            const dropped = e.dataTransfer.files?.[0];
            if (dropped) onFileSelected(dropped);
        },
        [onFileSelected],
    );

    if (file) {
        return (
            <div className="flex items-center justify-between rounded-md border p-3">
                <div className="flex items-center gap-2 text-sm">
                    <FileArchive className="h-4 w-4 text-muted-foreground" />
                    <span className="font-medium">{file.name}</span>
                    <span className="text-muted-foreground">({(file.size / 1024 / 1024).toFixed(1)} MB)</span>
                </div>
                <button
                    type="button"
                    onClick={() => {
                        onFileSelected(null);
                        if (inputRef.current) inputRef.current.value = "";
                    }}
                    className="text-muted-foreground hover:text-foreground"
                    aria-label="Remove file"
                >
                    <X className="h-4 w-4" />
                </button>
            </div>
        );
    }

    return (
        <div
            onDragOver={(e) => {
                e.preventDefault();
                setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={onDrop}
            onClick={() => inputRef.current?.click()}
            className={cn(
                "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-md border-2 border-dashed p-8 text-center transition-colors",
                isDragging ? "border-primary bg-primary/5" : "border-muted-foreground/25 hover:border-muted-foreground/50",
            )}
        >
            <Upload className="h-8 w-8 text-muted-foreground" />
            <p className="text-sm font-medium">Drag a bundle ZIP here, or click to browse</p>
            <p className="text-xs text-muted-foreground">Exported from Search &amp; Select → Export on another environment</p>
            <input
                ref={inputRef}
                type="file"
                accept={accept}
                className="hidden"
                onChange={(e) => onFileSelected(e.target.files?.[0] ?? null)}
            />
        </div>
    );
}
