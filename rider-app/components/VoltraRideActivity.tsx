/**
 * VoltraRideActivity — the iOS Live Activity UI (Voltra React components, no
 * Swift). Renders the rider's ride status on the Lock Screen (and Dynamic Island
 * on iPhone 14 Pro+). Drives BOTH:
 *   - the on-device activity (services/rideVoltraLiveActivity.ts), and
 *   - the build-time template generator (scripts/render-voltra-templates.mjs),
 *     whose output the Python backend pushes via APNs.
 *
 * ⚠️ BUILD-TIME VERIFY against the INSTALLED node_modules/@use-voltra/ios-client
 * types (the Phase 3 research flagged these as NOT fully documented):
 *   1. The exact Voltra primitives + style-prop names (VStack/HStack/Text/Symbol;
 *      SwiftUI-ish styles like cornerRadius/foregroundColor vs RN borderRadius/color).
 *   2. The Dynamic Island region API (nested island.compact.leading/… vs flat
 *      props). The Lock Screen layout below uses only the well-attested
 *      VStack/Text primitives; the Dynamic Island is left as a marked TODO so we
 *      don't ship speculative API. Lock Screen alone is a valid Live Activity.
 */
import { Voltra } from '@use-voltra/ios-client';
import type { VoltraContent } from '../services/rideVoltraLiveActivity';

function etaText(min?: number | null): string {
    if (!min || min <= 0) return '';
    return min === 1 ? '1 min' : `${min} min`;
}

export default function VoltraRideActivity(content: VoltraContent) {
    const eta = etaText(content.etaMinutes);
    const sub = [eta, content.vehicleLabel].filter(Boolean).join(' · ');

    // Lock Screen layout (well-attested Voltra primitives).
    return (
        <Voltra.VStack style={{ padding: 14, backgroundColor: '#0B1220', borderRadius: 16 }}>
            <Voltra.Text style={{ color: '#FFFFFF', fontSize: 17, fontWeight: '700' }}>
                {content.headline}
            </Voltra.Text>
            {sub ? (
                <Voltra.Text style={{ color: '#9CA3AF', fontSize: 14 }}>{sub}</Voltra.Text>
            ) : null}
            {content.dropoffArea ? (
                <Voltra.Text style={{ color: '#6B7280', fontSize: 13 }}>To {content.dropoffArea}</Voltra.Text>
            ) : null}
        </Voltra.VStack>
    );

    // TODO (build-time): add the Dynamic Island regions — compact.leading (status
    // icon), compact.trailing + minimal (ETA pill), expanded (headline + driver/
    // vehicle) — once the installed Voltra island-region API is confirmed.
}
