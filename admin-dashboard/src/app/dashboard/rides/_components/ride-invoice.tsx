"use client";

import { useState } from "react";
import { sendRideInvoice, getRideInvoice } from "@/lib/api";
import { Send, Download } from "lucide-react";

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
            // Generate PDF using jspdf
            const { jsPDF } = await import("jspdf");
            const doc = new jsPDF();

            const margin = 20;
            let y = margin;
            const lineH = 7;

            // Header
            doc.setFontSize(20);
            doc.setFont("helvetica", "bold");
            doc.text("Spinr - Ride Invoice", margin, y);
            y += 12;

            doc.setFontSize(10);
            doc.setFont("helvetica", "normal");
            doc.setTextColor(100);
            doc.text(`Ride ID: ${data.ride_id}`, margin, y);
            y += lineH;
            doc.text(`Date: ${data.ride_completed_at ? new Date(data.ride_completed_at).toLocaleString() : "—"}`, margin, y);
            y += lineH;
            doc.text(`Status: ${data.status?.toUpperCase()}`, margin, y);
            y += 12;

            // Route
            doc.setTextColor(0);
            doc.setFont("helvetica", "bold");
            doc.text("Route", margin, y);
            y += lineH;
            doc.setFont("helvetica", "normal");
            doc.text(`Pickup: ${data.pickup_address}`, margin, y);
            y += lineH;
            doc.text(`Dropoff: ${data.dropoff_address}`, margin, y);
            y += lineH;
            doc.text(`Distance: ${data.distance_km?.toFixed(1)} km | Duration: ${data.duration_minutes} min`, margin, y);
            y += 12;

            // Rider & Driver
            doc.setFont("helvetica", "bold");
            doc.text("Rider", margin, y);
            y += lineH;
            doc.setFont("helvetica", "normal");
            doc.text(`${data.rider_name} | ${data.rider_phone} | ${data.rider_email || "—"}`, margin, y);
            y += 10;
            doc.setFont("helvetica", "bold");
            doc.text("Driver", margin, y);
            y += lineH;
            doc.setFont("helvetica", "normal");
            doc.text(`${data.driver_name} | ${data.driver_phone} | ${data.driver_vehicle} ${data.driver_license_plate}`, margin, y);
            y += 12;

            // Fare Breakdown
            doc.setFont("helvetica", "bold");
            doc.text("Fare Breakdown", margin, y);
            y += lineH;
            doc.setFont("helvetica", "normal");

            const fareLines = [
                ["Base Fare", `$${data.base_fare?.toFixed(2)}`],
                ["Distance Fare", `$${data.distance_fare?.toFixed(2)}`],
                ["Time Fare", `$${data.time_fare?.toFixed(2)}`],
                ["Booking Fee", `$${data.booking_fee?.toFixed(2)}`],
            ];
            if (data.airport_fee > 0) fareLines.push(["Airport Fee", `$${data.airport_fee?.toFixed(2)}`]);
            if (data.surge_multiplier > 1) fareLines.push(["Surge", `${data.surge_multiplier}x`]);
            fareLines.push(["", ""]);
            fareLines.push(["Total Fare", `$${data.total_fare?.toFixed(2)}`]);
            if (data.tip_amount > 0) fareLines.push(["Tip", `$${data.tip_amount?.toFixed(2)}`]);

            for (const [label, val] of fareLines) {
                if (!label) { y += 3; continue; }
                doc.text(label, margin, y);
                doc.text(val, 180, y, { align: "right" });
                y += lineH;
            }

            y += 8;
            // Revenue Split
            doc.setFont("helvetica", "bold");
            doc.text("Revenue Split", margin, y);
            y += lineH;
            doc.setFont("helvetica", "normal");
            doc.text(`Driver Earnings: $${data.driver_earnings?.toFixed(2)}`, margin, y);
            y += lineH;
            doc.text(`Platform Earnings: $${data.admin_earnings?.toFixed(2)}`, margin, y);
            y += lineH;
            doc.text(`Payment Method: ${data.payment_method} | Status: ${data.payment_status}`, margin, y);

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
