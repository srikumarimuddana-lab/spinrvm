// src/app/dashboard/monitoring/alert-feed.tsx
"use client";

import { useState } from "react";
import { AlertEvent } from "./types";
import { Button } from "@/components/ui/button";
import { ChevronDown, ChevronUp, Zap, X } from "lucide-react";

interface AlertFeedProps {
    events: AlertEvent[];
    onClear: () => void;
    onEventClick: (event: AlertEvent) => void;
}

const ICON_MAP: Record<AlertEvent["icon"], string> = {
    online: "🟢",
    offline: "🔴",
    ride_new: "🟡",
    ride_done: "✅",
    ride_cancelled: "❌",
};

function formatTime(iso: string): string {
    return new Date(iso).toLocaleTimeString("en-CA", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
    });
}

export function AlertFeed({ events, onClear, onEventClick }: AlertFeedProps) {
    const [open, setOpen] = useState(false);
    const [lastSeenCount, setLastSeenCount] = useState(0);

    const unread = events.length - lastSeenCount;

    const handleOpen = () => {
        setOpen(true);
        setLastSeenCount(events.length);
    };

    return (
        <div className="border-t border-border bg-background">
            {/* Toggle pill */}
            <div className="flex items-center justify-between px-4 py-1.5">
                <button
                    onClick={open ? () => setOpen(false) : handleOpen}
                    className="flex items-center gap-2 text-sm font-medium text-foreground"
                >
                    <Zap className="h-4 w-4 text-amber-500" />
                    Live Events
                    {unread > 0 && !open && (
                        <span className="rounded-full bg-destructive px-1.5 py-0.5 text-xs font-bold text-destructive-foreground">
                            {unread}
                        </span>
                    )}
                    {open ? (
                        <ChevronDown className="h-4 w-4" />
                    ) : (
                        <ChevronUp className="h-4 w-4" />
                    )}
                </button>
                {open && events.length > 0 && (
                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={onClear}
                        className="h-6 gap-1 text-xs text-muted-foreground"
                    >
                        <X className="h-3 w-3" /> Clear all
                    </Button>
                )}
            </div>

            {/* Event list */}
            {open && (
                <div className="h-44 overflow-y-auto px-4 pb-2">
                    {events.length === 0 ? (
                        <p className="pt-4 text-center text-xs text-muted-foreground">
                            No events yet
                        </p>
                    ) : (
                        <div className="flex flex-col-reverse gap-0.5">
                            {events.map((evt) => (
                                <button
                                    key={evt.id}
                                    onClick={() => onEventClick(evt)}
                                    className="flex items-center gap-2 rounded px-1 py-0.5 text-left text-xs hover:bg-muted"
                                >
                                    <span className="shrink-0 font-mono text-muted-foreground">
                                        {formatTime(evt.timestamp)}
                                    </span>
                                    <span>{ICON_MAP[evt.icon]}</span>
                                    <span className="truncate">{evt.message}</span>
                                </button>
                            ))}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
