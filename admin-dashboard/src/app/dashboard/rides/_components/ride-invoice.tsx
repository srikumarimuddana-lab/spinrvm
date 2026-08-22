"use client";

import { useState } from "react";
import { sendRideInvoice, sendPayableRideInvoice, getRideInvoice, getRideRouteMapDataUrl } from "@/lib/api";
import { Send, Download } from "lucide-react";
import { useToast } from "@/components/ui/use-toast";
import { computePhaseDistances } from "./ride-ui-helpers";

interface Props {
    rideId: string;
    status: string;
    paymentStatus?: string;
}

// Safe number formatter — returns em-dash for null/undefined/NaN.
const fmt = (n: any, digits = 1): string =>
    typeof n === "number" && Number.isFinite(n) ? n.toFixed(digits) : "—";

const fmtMoney = (n: any): string =>
    typeof n === "number" && Number.isFinite(n) ? `$${n.toFixed(2)}` : "—";

// Coerce to a finite number (the backend serializes Decimal money as strings).
const num = (n: any): number => {
    if (typeof n === "number" && Number.isFinite(n)) return n;
    const p = parseFloat(String(n));
    return Number.isFinite(p) ? p : 0;
};

// Spinr brand red (#ee2b2b) as an RGB triple for jsPDF fills/text.
const BRAND: [number, number, number] = [238, 43, 43];

// Pragmatic email check — mirrors the backend send-receipt validation so the
// admin gets immediate feedback before the request round-trips.
const EMAIL_RE = /^[^@\s\x00]+@[^@\s\x00]+\.[^@\s\x00]+$/;

export default function RideInvoice({ rideId, status, paymentStatus }: Props) {
    const { toast } = useToast();
    const [sending, setSending] = useState(false);
    const [downloading, setDownloading] = useState(false);
    const [showEmailInput, setShowEmailInput] = useState(false);
    const [customEmail, setCustomEmail] = useState("");

    if (status !== "completed") return null;

    // Unpaid completed ride → send a PAYABLE Stripe invoice (rider pays the
    // hosted page, invoice.paid settles the ride). Paid/waived → re-email the
    // receipt as before.
    const isUnpaid = !!paymentStatus && !["paid", "waived_admin"].includes(paymentStatus);

    const handleSend = async () => {
        const overrideEmail = customEmail.trim();
        if (overrideEmail && !EMAIL_RE.test(overrideEmail)) {
            toast({ title: "Invalid email", description: "Enter a valid email address.", variant: "destructive" });
            return;
        }
        setSending(true);
        try {
            if (isUnpaid) {
                const res = await sendPayableRideInvoice(rideId);
                toast({
                    title: "Payable invoice sent",
                    description: res?.invoice_url
                        ? "Stripe emailed the rider a pay link. The ride settles automatically once paid."
                        : "Stripe emailed the rider a pay link.",
                });
            } else {
                await sendRideInvoice(rideId, overrideEmail || undefined);
                toast({
                    title: "Invoice sent",
                    description: overrideEmail
                        ? `Receipt sent to ${overrideEmail}.`
                        : "Receipt sent to rider's email.",
                });
                setShowEmailInput(false);
                setCustomEmail("");
            }
        } catch {
            toast({
                title: isUnpaid ? "Failed to send payable invoice" : "Failed to send invoice",
                variant: "destructive",
            });
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

            // Branded header band (matches the red Spinr email receipt)
            doc.setFillColor(...BRAND);
            doc.rect(0, 0, pageW, 30, "F");
            doc.setTextColor(255, 255, 255);
            doc.setFontSize(24);
            doc.setFont("helvetica", "bold");
            doc.text("Spinr", margin, 15);
            doc.setFontSize(11);
            doc.setFont("helvetica", "normal");
            doc.text("Ride Receipt", margin, 23);
            doc.setFontSize(8);
            doc.text("Spinr Mobility Inc.", pageW - margin, 13, { align: "right" });
            doc.text("Saskatoon, SK", pageW - margin, 18, { align: "right" });
            doc.text("support@spinr.ca · www.spinr.ca", pageW - margin, 23, { align: "right" });
            y = 40;

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
            doc.text(
                `Status: ${(data.status ?? "—").toUpperCase()}   ·   Payment: ${(
                    data.payment_method || "card"
                ).toUpperCase()} (${(data.payment_status || "pending").toUpperCase()})`,
                margin,
                y
            );
            y += 2;

            // Separator
            doc.setDrawColor(225);
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
            // PIPEDA: no driver personal phone/plate. Show the app-wide
            // driver_code (support reference) + vehicle instead.
            doc.text(data.driver_code || "—", pageW / 2, y);
            y += 5;
            doc.text(data.rider_email || "—", margin, y);
            doc.text(data.driver_vehicle || "—", pageW / 2, y);
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
            // Dynamic area fees stored in area_fees_breakdown JSONB at booking time.
            // Each entry: { name, calculated_value, ... }
            for (const fee of (Array.isArray(data.area_fees_breakdown) ? data.area_fees_breakdown : [])) {
                const amount = typeof fee.calculated_value === "number"
                    ? fee.calculated_value
                    : parseFloat(String(fee.calculated_value ?? 0));
                if (Number.isFinite(amount) && amount > 0) {
                    fareLines.push([fee.name || "Fee", fmtMoney(amount)]);
                }
            }

            doc.setTextColor(80);
            for (const [label, val] of fareLines) {
                ensureSpace(lineH);
                doc.text(label, margin, y);
                doc.text(val, pageW - margin, y, { align: "right" });
                y += lineH;
            }

            // Subtotal + taxes (GST/PST as separate line items — SK regulatory)
            ensureSpace(lineH * 4);
            y += 2;
            doc.setDrawColor(225);
            doc.line(margin, y, pageW - margin, y);
            y += 6;
            doc.setFontSize(9);
            doc.setFont("helvetica", "normal");
            doc.setTextColor(80);
            doc.text("Subtotal", margin, y);
            doc.text(fmtMoney(num(data.total_fare)), pageW - margin, y, { align: "right" });
            y += lineH;

            // Prefer the persisted tax_breakdown so the lines reconcile to what
            // was charged; fall back to the grand_total gap for legacy rides.
            const taxBreakdown =
                data.tax_breakdown && typeof data.tax_breakdown === "object" ? data.tax_breakdown : {};
            const taxLines: [string, string][] = [];
            for (const [label, payload] of Object.entries(taxBreakdown)) {
                const amount = num((payload as any)?.amount);
                const rate = num((payload as any)?.rate);
                if (amount === 0) continue;
                taxLines.push([`${label}${rate ? ` (${rate.toFixed(0)}%)` : ""}`, fmtMoney(amount)]);
            }
            if (taxLines.length === 0) {
                const gap = num(data.grand_total) - num(data.total_fare);
                if (gap > 0.005) taxLines.push(["Tax", fmtMoney(gap)]);
            }
            for (const [label, val] of taxLines) {
                ensureSpace(lineH);
                doc.text(label, margin, y);
                doc.text(val, pageW - margin, y, { align: "right" });
                y += lineH;
            }

            if (num(data.tip_amount) > 0) {
                ensureSpace(lineH);
                doc.setTextColor(16, 130, 90);
                doc.text("Tip", margin, y);
                doc.text(fmtMoney(num(data.tip_amount)), pageW - margin, y, { align: "right" });
                doc.setTextColor(80);
                y += lineH;
            }

            // Grand total — red highlighted box (tax-inclusive, plus tip).
            const grandTotal = num(data.grand_total) + num(data.tip_amount);
            ensureSpace(15);
            y += 2;
            doc.setFillColor(...BRAND);
            doc.roundedRect(margin, y, pageW - 2 * margin, 11, 1.5, 1.5, "F");
            doc.setTextColor(255, 255, 255);
            doc.setFont("helvetica", "bold");
            doc.setFontSize(12);
            doc.text("Total Paid", margin + 4, y + 7.4);
            doc.text(`${fmtMoney(grandTotal)} CAD`, pageW - margin - 4, y + 7.4, { align: "right" });
            doc.setTextColor(0);
            y += 18;

            // GST/PST registration + thank-you note
            doc.setFont("helvetica", "normal");
            doc.setFontSize(8);
            doc.setTextColor(140);
            doc.text("Taxes shown are GST/PST collected by the driver and remitted as required.", margin, y);

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
            toast({ title: "Failed to download invoice", variant: "destructive" });
        } finally {
            setDownloading(false);
        }
    };

    return (
        <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2">
                {/* Rendered inside the Payment card (light surface) — filled primary
                 *  for the main action, outline for the secondary download. */}
                <button onClick={handleSend} disabled={sending}
                    className="flex items-center gap-1.5 text-xs font-semibold bg-primary text-primary-foreground hover:bg-primary/90 px-3 py-1.5 rounded-lg shadow-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
                    <Send className="h-3.5 w-3.5" /> {sending ? "Sending..." : isUnpaid ? "Send Payable Invoice" : "Send Invoice"}
                </button>
                {/* eslint-disable-next-line no-restricted-syntax -- decorative secondary-button accent (outline vs the primary "Send" button above), not a success/warning/destructive signal (#2816) */}
                <button onClick={handleDownload} disabled={downloading}
                    className="flex items-center gap-1.5 text-xs font-semibold border border-emerald-300 text-emerald-700 hover:bg-emerald-50 dark:border-emerald-800 dark:text-emerald-400 dark:hover:bg-emerald-900/20 px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
                    <Download className="h-3.5 w-3.5" /> {downloading ? "Generating..." : "Download PDF"}
                </button>
            </div>
            {/* "Send to a different email" only applies to the receipt path. The
             *  payable Stripe invoice is emailed by Stripe to the rider's
             *  customer address and can't be re-targeted here. */}
            {!isUnpaid && (
                showEmailInput ? (
                    <div className="flex items-center gap-2">
                        <input
                            type="email"
                            value={customEmail}
                            onChange={(e) => setCustomEmail(e.target.value)}
                            placeholder="name@example.com"
                            className="flex-1 text-xs px-2 py-1.5 rounded-lg border border-input bg-background"
                        />
                        <button
                            onClick={() => { setShowEmailInput(false); setCustomEmail(""); }}
                            className="text-xs font-medium text-muted-foreground hover:text-foreground px-2 py-1.5">
                            Cancel
                        </button>
                    </div>
                ) : (
                    <button
                        onClick={() => setShowEmailInput(true)}
                        className="self-start text-xs font-medium text-primary hover:underline">
                        Send to a different email
                    </button>
                )
            )}
        </div>
    );
}
