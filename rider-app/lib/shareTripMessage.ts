/**
 * Builds the "share my trip" safety message. This text is sent to a rider's
 * emergency contacts / family, so every line must be REAL data or omitted —
 * never a placeholder. (Regression: the message used to hardcode
 * "4th Avenue North" as the current location and fall back to
 * "1055 Canada Place" for the destination.) The live-tracking link is the
 * authoritative real-time location; the static lines are context only.
 */

export interface ShareTripArgs {
  driverName?: string | null;
  driverRating?: string | number | null;
  vehicleColor?: string | null;
  vehicleMake?: string | null;
  vehicleModel?: string | null;
  licensePlate?: string | null;
  dropoffAddress?: string | null;
  estimatedTime: string;
  etaMinutes: number;
  rideCode?: string | null;
  liveTrackingUrl: string;
}

export function buildShareTripMessage(a: ShareTripArgs): string {
  // Include the destination only when we actually know it.
  const headingTo = a.dropoffAddress ? `📍 HEADING TO: ${a.dropoffAddress}\n\n` : '';
  // Human-readable ride code so the recipient can quote it to support
  // without guessing at a truncated UUID.
  const rideRef = a.rideCode ? `🆔 RIDE CODE: ${a.rideCode}\n` : '';
  return `
🚗 TRACK MY SPINR RIDE - LIVE LOCATION

👤 DRIVER: ${a.driverName || 'Unknown'}
⭐ RATING: ${a.driverRating || 'New'}

🚙 VEHICLE: ${a.vehicleColor || ''} ${a.vehicleMake || 'Unknown'} ${a.vehicleModel || 'Vehicle'}
📋 LICENSE PLATE: ${a.licensePlate || 'Pending'}

${headingTo}⏱️ ESTIMATED ARRIVAL: ${a.estimatedTime} (${a.etaMinutes} min left)

${rideRef}🔴 LIVE TRACKING LINK:
${a.liveTrackingUrl}

I've shared my live location with you for safety.
`.trim();
}
