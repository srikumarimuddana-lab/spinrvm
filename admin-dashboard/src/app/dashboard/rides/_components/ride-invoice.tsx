"use client";

import { useState } from "react";
import { sendRideInvoice, getRideInvoice } from "@/lib/api";
import { Send, Download } from "lucide-react";
import { computePhaseDistances } from "./ride-ui-helpers";

interface Props {
    rideId: string;
    status: string;
}

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
            let y = margin;
            const lineH = 7;

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
            doc.text(`Invoice #: ${data.ride_id?.slice(0, 12)}`, margin, y);
            doc.text(`Date: ${data.ride_completed_at ? new Date(data.ride_completed_at).toLocaleString() : "—"}`, pageW - margin, y, { align: "right" });
            y += 6;
            doc.text(`Status: ${data.status?.toUpperCase()}`, margin, y);
            y += 2;

            // Separator
            doc.setDrawColor(200);
            doc.line(margin, y + 3, pageW - margin, y + 3);
            y += 10;

            // Route section with map placeholder
            doc.setFontSize(11);
            doc.setFont("helvetica", "bold");
            doc.setTextColor(0);
            doc.text("Route Details", margin, y);
            y += 8;

            doc.setFontSize(9);
            doc.setFont("helvetica", "normal");

            // Pickup
            doc.setFillColor(16, 185, 129); // emerald
            doc.circle(margin + 3, y - 1.5, 2, "F");
            doc.text("PICKUP", margin + 8, y);
            y += lineH;
            doc.setTextColor(60);
            doc.text(data.pickup_address || "—", margin + 8, y);
            y += lineH + 2;

            // Dropoff
            doc.setTextColor(0);
            doc.setFillColor(59, 130, 246); // blue
            doc.circle(margin + 3, y - 1.5, 2, "F");
            doc.text("DROPOFF", margin + 8, y);
            y += lineH;
            doc.setTextColor(60);
            doc.text(data.dropoff_address || "—", margin + 8, y);
            y += lineH + 2;

            // Route stats
            doc.setTextColor(0);
            doc.setFont("helvetica", "normal");
            const statsText = `Distance: ${data.distance_km?.toFixed(1)} km  |  Duration: ${data.duration_minutes} min${data.surge_multiplier > 1 ? `  |  Surge: ${data.surge_multiplier}x` : ""}`;
            doc.text(statsText, margin + 8, y);
            y += lineH;

            // Map coordinates (for reference)
            doc.setFontSize(8);
            doc.setTextColor(150);
            if (data.pickup_lat) {
                doc.text(`Pickup: ${data.pickup_lat?.toFixed(5)}, ${data.pickup_lng?.toFixed(5)}  |  Dropoff: ${data.dropoff_lat?.toFixed(5)}, ${data.dropoff_lng?.toFixed(5)}`, margin + 8, y);
                y += 5;
            }

            // Static map link
            doc.setTextColor(59, 130, 246);
            doc.setFontSize(8);
            if (data.pickup_lat && data.dropoff_lat) {
                const mapUrl = `https://www.openstreetmap.org/directions?from=${data.pickup_lat},${data.pickup_lng}&to=${data.dropoff_lat},${data.dropoff_lng}`;
                doc.textWithLink("View route on map", margin + 8, y, { url: mapUrl });
                y += 4;
            }

            // Actual vs estimated distance
            if (data.actual_distance_km && data.actual_distance_km !== data.distance_km) {
                doc.setTextColor(100);
                doc.setFontSize(8);
                y += 2;
                doc.text(`Actual distance traveled: ${data.actual_distance_km.toFixed(2)} km (estimated: ${data.distance_km?.toFixed(1)} km)`, margin + 8, y);
                y += 4;
            }

            // Route map image from GPS trail
            if (data.location_trail && data.location_trail.length > 1 && data.pickup_lat) {
                y += 4;
                doc.setTextColor(0);
                doc.setFontSize(9);
                doc.setFont("helvetica", "bold");
                doc.text("Route Taken", margin + 8, y);
                y += 2;
                doc.setFont("helvetica", "normal");

                try {
                    // Build a static map URL using GPS trail points
                    // Sample trail to ~30 points for URL length
                    const trail = data.location_trail;
                    const step = Math.max(1, Math.floor(trail.length / 30));
                    const sampled = trail.filter((_: any, i: number) => i % step === 0 || i === trail.length - 1);
                    const pathCoords = sampled.map((p: any) => `${p.lat},${p.lng}`).join("|");

                    const staticMapUrl = `https://maps.googleapis.com/maps/api/staticmap?size=500x200&maptype=roadmap`
                        + `&markers=color:green|label:P|${data.pickup_lat},${data.pickup_lng}`
                        + `&markers=color:red|label:D|${data.dropoff_lat},${data.dropoff_lng}`
                        + `&path=color:0x3B82F6ff|weight:3|${pathCoords}`
                        + `&key=${process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY || ""}`;

                    // Fetch and embed the static map image
                    const imgResponse = await fetch(staticMapUrl);
                    if (imgResponse.ok) {
                        const imgBlob = await imgResponse.blob();
                        const imgBase64 = await new Promise<string>((resolve) => {
                            const reader = new FileReader();
                            reader.onloadend = () => resolve(reader.result as string);
                            reader.readAsDataURL(imgBlob);
                        });
                        doc.addImage(imgBase64, "PNG", margin, y, pageW - 2 * margin, 50);
                        y += 54;
                    }
                } catch (mapErr) {
                    console.log("Failed to embed route map in invoice:", mapErr);
                }
            }

            y += 6;
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
            doc.text(`${data.driver_vehicle || ""} ${data.driver_license_plate || ""}`.trim() || "—", pageW / 2, y);
            y += 10;

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
                ["Base Fare", `$${(data.base_fare || 0).toFixed(2)}`],
                [`Distance (${data.distance_km?.toFixed(1)} km)`, `$${(data.distance_fare || 0).toFixed(2)}`],
                [`Time (${data.duration_minutes} min)`, `$${(data.time_fare || 0).toFixed(2)}`],
                ["Booking Fee", `$${(data.booking_fee || 0).toFixed(2)}`],
            ];
            if ((data.airport_fee || 0) > 0) fareLines.push(["Airport Fee", `$${data.airport_fee.toFixed(2)}`]);

            doc.setTextColor(80);
            for (const [label, val] of fareLines) {
                doc.text(label, margin, y);
                doc.text(val, pageW - margin, y, { align: "right" });
                y += lineH;
            }

            // Total
            y += 2;
            doc.setDrawColor(200);
            doc.line(margin, y, pageW - margin, y);
            y += 6;
            doc.setFont("helvetica", "bold");
            doc.setFontSize(12);
            doc.setTextColor(0);
            doc.text("Total", margin, y);
            doc.text(`$${(data.total_fare || 0).toFixed(2)}`, pageW - margin, y, { align: "right" });
            y += lineH;

            if ((data.tip_amount || 0) > 0) {
                doc.setFontSize(9);
                doc.setFont("helvetica", "normal");
                doc.setTextColor(180, 130, 0);
                doc.text("Tip", margin, y);
                doc.text(`$${data.tip_amount.toFixed(2)}`, pageW - margin, y, { align: "right" });
                y += lineH;
            }

            y += 4;
            doc.setFontSize(9);
            doc.setFont("helvetica", "normal");
            doc.setTextColor(100);
            doc.text(`Payment: ${(data.payment_method || "card").toUpperCase()}  |  Status: ${(data.payment_status || "pending").toUpperCase()}`, margin, y);

            // Phase distance log
            if (data.location_trail && data.location_trail.length > 1) {
                const phases = computePhaseDistances(data.location_trail);
                if (phases.length > 0) {
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
                        const label = phaseLabels[p.phase] || p.phase.replace(/_/g, " ");
                        doc.text(label, margin, y);
                        doc.text(`${p.distance_km} km (${p.points} GPS pts)`, pageW - margin, y, { align: "right" });
                        y += lineH;
                    }

                    // Total GPS distance
                    y += 2;
                    doc.setFont("helvetica", "bold");
                    doc.setTextColor(0);
                    const totalGps = phases.reduce((s, p) => s + p.distance_km, 0);
                    doc.text("Total GPS Distance", margin, y);
                    doc.text(`${totalGps.toFixed(2)} km`, pageW - margin, y, { align: "right" });
                    y += lineH;
                }
            }

            // Footer — check if we need a new page
            if (y > 265) {
                doc.addPage();
                y = margin;
            } else {
                y = 280;
            }
            doc.setFontSize(8);
            doc.setTextColor(160);
            doc.text("Thank you for riding with Spinr!", pageW / 2, y, { align: "center" });

            doc.save(`spinr-invoice-${data.ride_id?.slice(0, 8)}.pdf`);
        } catch (e) {
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
