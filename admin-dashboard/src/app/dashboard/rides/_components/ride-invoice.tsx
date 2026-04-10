"use client";

import { useState } from "react";
import { sendRideInvoice, getRideInvoice, getRideRouteMapDataUrl } from "@/lib/api";
import { Send, Download } from "lucide-react";
import { computePhaseDistances } from "./ride-ui-helpers";

interface Props {
    rideId: string;
    status: string;
}

// Safe number formatter — returns em-dash for null/undefined/NaN.
const fmt = (n: any, digits = 1): string =>
    typeof n === "number" && Number.isFinite(n) ? n.toFixed(digits) : "—";

const fmtMoney = (n: any): string =>
    typeof n === "number" && Number.isFinite(n) ? `$${n.toFixed(2)}` : "—";

export default function RideInvoice({ rideId, status }: Props) {
    const [sending, setSending] = useState(false);
    const [downloading, setDownloading] = useState(false);

    if (status !== "completed") return null;

    const handleSend = async () => {
        setSending(true);
        try {
            await sendRideInvoice(rideId);
            alert("Invoice/receipt sent to rider's email");
        } catch {
            alert("Failed to send invoice");
        } finally {
            setSending(false);
        }
    };

    const handleDownload = async () => {
        setDownloading(true);
        try {
            const data = await getRideInvoice(rideId);
            const { jsPDF } = await import("jspdf");
            const doc = new jsPDF();

            const margin = 20;
            const pageW = 210;
            const pageH = 297;
            const bottomLimit = 280;
            let y = margin;
            const lineH = 7;

            // Helper: ensure there's enough vertical space; auto-page-break.
            const ensureSpace = (needed: number) => {
                if (y + needed > bottomLimit) {
                    doc.addPage();
                    y = margin;
                }
            };

            // Header
            doc.setFontSize(22);
            doc.setFont("helvetica", "bold");
            doc.text("SPINR", margin, y);
            doc.setFontSize(12);
            doc.setFont("helvetica", "normal");
            doc.setTextColor(100);
            doc.text("Ride Invoice", margin + 50, y);
            y += 14;

            // Ride info
            doc.setFontSize(9);
            doc.setTextColor(120);
            doc.text(`Invoice #: ${data.ride_id?.slice(0, 12) ?? "—"}`, margin, y);
            doc.text(
                `Date: ${data.ride_completed_at ? new Date(data.ride_completed_at).toLocaleString() : "—"}`,
                pageW - margin,
                y,
                { align: "right" }
            );
            y += 6;
            doc.text(`Status: ${(data.status ?? "—").toUpperCase()}`, margin, y);
            y += 2;

            // Separator
            doc.setDrawColor(200);
            doc.line(margin, y + 3, pageW - margin, y + 3);
            y += 10;

            // Route section
            ensureSpace(40);
            doc.setFontSize(11);
            doc.setFont("helvetica", "bold");
            doc.setTextColor(0);
            doc.text("Route Details", margin, y);
            y += 8;

            doc.setFontSize(9);
            doc.setFont("helvetica", "normal");

            // Pickup
            doc.setFillColor(16, 185, 129);
            doc.circle(margin + 3, y - 1.5, 2, "F");
            doc.text("PICKUP", margin + 8, y);
            y += lineH;
            doc.setTextColor(60);
            doc.text(data.pickup_address || "—", margin + 8, y);
            y += lineH + 2;

            // Dropoff
            doc.setTextColor(0);
            doc.setFillColor(59, 130, 246);
            doc.circle(margin + 3, y - 1.5, 2, "F");
            doc.text("DROPOFF", margin + 8, y);
            y += lineH;
            doc.setTextColor(60);
            doc.text(data.dropoff_address || "—", margin + 8, y);
            y += lineH + 2;

            // Route stats
            doc.setTextColor(0);
            doc.setFont("helvetica", "normal");
            const surgeTxt =
                typeof data.surge_multiplier === "number" && data.surge_multiplier > 1
                    ? `  |  Surge: ${data.surge_multiplier}x`
                    : "";
            const statsText = `Distance: ${fmt(data.distance_km)} km  |  Duration: ${fmt(
                data.duration_minutes,
                0
            )} min${surgeTxt}`;
            doc.text(statsText, margin + 8, y);
            y += lineH;

            // Map coordinates (for reference)
            doc.setFontSize(8);
            doc.setTextColor(150);
            if (typeof data.pickup_lat === "number" && typeof data.dropoff_lat === "number") {
                doc.text(
                    `Pickup: ${fmt(data.pickup_lat, 5)}, ${fmt(data.pickup_lng, 5)}  |  Dropoff: ${fmt(
                        data.dropoff_lat,
                        5
                    )}, ${fmt(data.dropoff_lng, 5)}`,
                    margin + 8,
                    y
                );
                y += 5;
            }

            // Static map link (OpenStreetMap — no API key needed)
            doc.setTextColor(59, 130, 246);
            doc.setFontSize(8);
            if (typeof data.pickup_lat === "number" && typeof data.dropoff_lat === "number") {
                const mapUrl = `https://www.openstreetmap.org/directions?from=${data.pickup_lat},${data.pickup_lng}&to=${data.dropoff_lat},${data.dropoff_lng}`;
                doc.textWithLink("View route on map", margin + 8, y, { url: mapUrl });
                y += 4;
            }

            // Actual vs estimated distance
            if (
                typeof data.actual_distance_km === "number" &&
                Number.isFinite(data.actual_distance_km) &&
                data.actual_distance_km !== data.distance_km
            ) {
                doc.setTextColor(100);
                doc.setFontSize(8);
                y += 2;
                doc.text(
                    `Actual distance traveled: ${fmt(data.actual_distance_km, 2)} km (estimated: ${fmt(
                        data.distance_km
                    )} km)`,
                    margin + 8,
                    y
                );
                y += 4;
            }

            // Route map image from GPS trail — fetched via secure backend proxy
            if (
                Array.isArray(data.location_trail) &&
                data.location_trail.length > 1 &&
                typeof data.pickup_lat === "number"
            ) {
                ensureSpace(68);
                y += 4;
                doc.setTextColor(0);
                doc.setFontSize(9);
                doc.setFont("helvetica", "bold");
                doc.text("Route Taken", margin + 8, y);
                y += 2;
                doc.setFont("helvetica", "normal");

                const dataUrl = await getRideRouteMapDataUrl(rideId);
                if (dataUrl) {
                    doc.addImage(dataUrl, "PNG", margin, y, pageW - 2 * margin, 50);
                    y += 54;
                } else {
                    doc.setTextColor(150);
                    doc.setFontSize(8);
                    doc.text("Route map unavailable", margin + 8, y + 6);
                    y += 12;
                }
            }

            y += 6;
            ensureSpace(30);
            doc.setDrawColor(200);
            doc.line(margin, y, pageW - margin, y);
            y += 8;

            // Rider & Driver
            doc.setFontSize(11);
            doc.setFont("helvetica", "bold");
            doc.setTextColor(0);
            doc.text("Rider", margin, y);
            doc.text("Driver", pageW / 2, y);
            y += lineH;
            doc.setFontSize(9);
            doc.setFont("helvetica", "normal");
            doc.setTextColor(60);
            doc.text(data.rider_name || "—", margin, y);
            doc.text(data.driver_name || "—", pageW / 2, y);
            y += 5;
            doc.text(data.rider_phone || "—", margin, y);
            doc.text(data.driver_phone || "—", pageW / 2, y);
            y += 5;
            doc.text(data.rider_email || "—", margin, y);
            doc.text(
                `${data.driver_vehicle || ""} ${data.driver_license_plate || ""}`.trim() || "—",
                pageW / 2,
                y
            );
            y += 10;

            ensureSpace(50);
            doc.setDrawColor(200);
            doc.line(margin, y, pageW - margin, y);
            y += 8;

            // Fare Breakdown
            doc.setFontSize(11);
            doc.setFont("helvetica", "bold");
            doc.setTextColor(0);
            doc.text("Fare Breakdown", margin, y);
            y += 8;
            doc.setFontSize(9);
            doc.setFont("helvetica", "normal");

            const fareLines: [string, string][] = [
                ["Base Fare", fmtMoney(data.base_fare)],
                [`Distance (${fmt(data.distance_km)} km)`, fmtMoney(data.distance_fare)],
                [`Time (${fmt(data.duration_minutes, 0)} min)`, fmtMoney(data.time_fare)],
                ["Booking Fee", fmtMoney(data.booking_fee)],
            ];
            if (typeof data.airport_fee === "number" && data.airport_fee > 0) {
                fareLines.push(["Airport Fee", fmtMoney(data.airport_fee)]);
            }

            doc.setTextColor(80);
            for (const [label, val] of fareLines) {
                ensureSpace(lineH);
                doc.text(label, margin, y);
                doc.text(val, pageW - margin, y, { align: "right" });
                y += lineH;
            }

            // Total
            ensureSpace(lineH * 2);
            y += 2;
            doc.setDrawColor(200);
            doc.line(margin, y, pageW - margin, y);
            y += 6;
            doc.setFont("helvetica", "bold");
            doc.setFontSize(12);
            doc.setTextColor(0);
            doc.text("Total", margin, y);
            doc.text(fmtMoney(data.total_fare), pageW - margin, y, { align: "right" });
            y += lineH;

            if (typeof data.tip_amount === "number" && data.tip_amount > 0) {
                doc.setFontSize(9);
                doc.setFont("helvetica", "normal");
                doc.setTextColor(180, 130, 0);
                doc.text("Tip", margin, y);
                doc.text(fmtMoney(data.tip_amount), pageW - margin, y, { align: "right" });
                y += lineH;
            }

            y += 4;
            doc.setFontSize(9);
            doc.setFont("helvetica", "normal");
            doc.setTextColor(100);
            doc.text(
                `Payment: ${(data.payment_method || "card").toUpperCase()}  |  Status: ${(
                    data.payment_status || "pending"
                ).toUpperCase()}`,
                margin,
                y
            );

            // Phase distance log
            if (Array.isArray(data.location_trail) && data.location_trail.length > 1) {
                const phases = computePhaseDistances(data.location_trail);
                if (phases.length > 0) {
                    ensureSpace(40);
                    y += 10;
                    doc.setDrawColor(200);
                    doc.line(margin, y, pageW - margin, y);
                    y += 8;

                    doc.setFontSize(11);
                    doc.setFont("helvetica", "bold");
                    doc.setTextColor(0);
                    doc.text("Distance Log by Phase", margin, y);
                    y += 8;

                    doc.setFontSize(9);
                    doc.setFont("helvetica", "normal");

                    const phaseLabels: Record<string, string> = {
                        navigating_to_pickup: "Navigating to Pickup",
                        arrived_at_pickup: "Waiting at Pickup",
                        trip_in_progress: "Trip in Progress",
                        online_idle: "Online Idle",
                    };

                    doc.setTextColor(80);
                    for (const p of phases) {
                        ensureSpace(lineH);
                        const label = phaseLabels[p.phase] || p.phase.replace(/_/g, " ");
                        doc.text(label, margin, y);
                        doc.text(`${p.distance_km} km (${p.points} GPS pts)`, pageW - margin, y, {
                            align: "right",
                        });
                        y += lineH;
                    }

                    ensureSpace(lineH);
                    y += 2;
                    doc.setFont("helvetica", "bold");
                    doc.setTextColor(0);
                    const totalGps = phases.reduce((s, p) => s + p.distance_km, 0);
                    doc.text("Total GPS Distance", margin, y);
                    doc.text(`${totalGps.toFixed(2)} km`, pageW - margin, y, { align: "right" });
                    y += lineH;
                }
            }

            // Footer — always on the last page
            const footerY = pageH - 17;
            doc.setFontSize(8);
            doc.setTextColor(160);
            doc.text("Thank you for riding with Spinr!", pageW / 2, footerY, { align: "center" });

            doc.save(`spinr-invoice-${data.ride_id?.slice(0, 8) ?? "ride"}.pdf`);
        } catch (e) {
            console.error("Invoice download failed:", e);
            alert("Failed to download invoice");
        } finally {
            setDownloading(false);
        }
    };

    return (
        <div className="flex gap-2">
            <button onClick={handleSend} disabled={sending}
                className="flex items-center gap-1 text-xs font-semibold text-primary hover:bg-primary/10 px-2.5 py-1.5 rounded-lg disabled:opacity-50">
                <Send className="h-3.5 w-3.5" /> {sending ? "Sending..." : "Send Invoice"}
            </button>
            <button onClick={handleDownload} disabled={downloading}
                className="flex items-center gap-1 text-xs font-semibold text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-900/20 px-2.5 py-1.5 rounded-lg disabled:opacity-50">
                <Download className="h-3.5 w-3.5" /> {downloading ? "Generating..." : "Download PDF"}
            </button>
        </div>
    );
}
