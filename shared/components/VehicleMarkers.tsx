import React from 'react';
import Svg, { Rect, Path, G } from 'react-native-svg';

/**
 * Top-down (bird's-eye) car SVG markers, one silhouette per vehicle tier.
 *
 * These are designed to be dropped inside a react-native-maps <Marker> and
 * rotated by the driver's heading. Every car is drawn nose-up (north) on a
 * 48×64 canvas so a `rotation={heading}` on the Marker points the car the
 * right way. Keep them lightweight — no gradients, no filters — so the
 * Android Marker snapshot stays cheap.
 *
 * The four tiers mirror backend/seed_vehicle_types.py (Economy / Premium /
 * Van / XL). `resolveVehicleMarker()` maps either the backend `icon` value
 * (car-compact, car-sport, bus, bus-outline) or the human name onto a
 * silhouette, falling back to the economy sedan for anything unknown.
 */

export type VehicleTier = 'economy' | 'premium' | 'van' | 'xl';

interface VehicleSvgProps {
    size?: number;
    /** Override the body colour; each tier has a sensible default. */
    color?: string;
}

const VIEWBOX = '0 0 48 64';

/** Four wheels drawn as dark rounded rects at the given axle Y positions. */
const Wheels: React.FC<{ left: number; right: number; frontY: number; rearY: number; w?: number; h?: number }> = ({
    left,
    right,
    frontY,
    rearY,
    w = 5,
    h = 11,
}) => (
    <G fill="#15151B">
        <Rect x={left} y={frontY} width={w} height={h} rx={2} />
        <Rect x={right} y={frontY} width={w} height={h} rx={2} />
        <Rect x={left} y={rearY} width={w} height={h} rx={2} />
        <Rect x={right} y={rearY} width={w} height={h} rx={2} />
    </G>
);

/** Economy — compact sedan (Spinr teal). icon: car-compact */
export const EconomyCar: React.FC<VehicleSvgProps> = ({ size = 44, color = '#00C9A7' }) => (
    <Svg width={size} height={size} viewBox={VIEWBOX}>
        <Rect x={10} y={9} width={30} height={52} rx={11} fill="rgba(0,0,0,0.18)" />
        <Wheels left={6} right={37} frontY={15} rearY={40} />
        <Rect x={9} y={6} width={30} height={52} rx={11} fill={color} stroke="rgba(0,0,0,0.22)" strokeWidth={1} />
        <Path d="M14 20 L34 20 L31 11 L17 11 Z" fill="#BFEFE6" opacity={0.92} />
        <Rect x={13} y={22} width={22} height={17} rx={4} fill="#ffffff" opacity={0.16} />
        <Path d="M15 41 L33 41 L31 49 L17 49 Z" fill="#BFEFE6" opacity={0.7} />
        <Rect x={12} y={7} width={5} height={3} rx={1.5} fill="#FFE08A" />
        <Rect x={31} y={7} width={5} height={3} rx={1.5} fill="#FFE08A" />
        <Rect x={12} y={54} width={5} height={3} rx={1.5} fill="#FF5A5A" />
        <Rect x={31} y={54} width={5} height={3} rx={1.5} fill="#FF5A5A" />
    </Svg>
);

/** Premium — sleek luxury sedan (graphite + gold accent). icon: car-sport */
export const PremiumCar: React.FC<VehicleSvgProps> = ({ size = 44, color = '#1B1B26' }) => (
    <Svg width={size} height={size} viewBox={VIEWBOX}>
        <Rect x={11} y={8} width={26} height={54} rx={13} fill="rgba(0,0,0,0.2)" />
        <Wheels left={7} right={36} frontY={15} rearY={41} />
        <Rect x={11} y={5} width={26} height={54} rx={13} fill={color} stroke="#E8C45A" strokeWidth={1.1} />
        <Path d="M15 21 L33 21 L30 12 L18 12 Z" fill="#7FC7E8" opacity={0.9} />
        <Rect x={15} y={23} width={18} height={17} rx={4} fill="#E8C45A" opacity={0.16} />
        <Path d="M16 42 L32 42 L30 50 L18 50 Z" fill="#7FC7E8" opacity={0.68} />
        <Rect x={13} y={6} width={5} height={3} rx={1.5} fill="#FFFFFF" />
        <Rect x={30} y={6} width={5} height={3} rx={1.5} fill="#FFFFFF" />
        <Rect x={13} y={55} width={5} height={3} rx={1.5} fill="#FF4D4D" />
        <Rect x={30} y={55} width={5} height={3} rx={1.5} fill="#FF4D4D" />
    </Svg>
);

/** Van — long minivan, boxier roof (blue). icon: bus */
export const VanCar: React.FC<VehicleSvgProps> = ({ size = 44, color = '#2F7DEB' }) => (
    <Svg width={size} height={size} viewBox={VIEWBOX}>
        <Rect x={9} y={6} width={32} height={56} rx={8} fill="rgba(0,0,0,0.18)" />
        <Wheels left={5} right={38} frontY={16} rearY={42} h={12} />
        <Rect x={8} y={3} width={32} height={56} rx={8} fill={color} stroke="rgba(0,0,0,0.22)" strokeWidth={1} />
        <Path d="M13 18 L35 18 L33 9 L15 9 Z" fill="#CFE5FF" opacity={0.92} />
        <Rect x={12} y={21} width={24} height={26} rx={4} fill="#ffffff" opacity={0.14} />
        <Rect x={11} y={24} width={4} height={18} rx={2} fill="#CFE5FF" opacity={0.6} />
        <Rect x={33} y={24} width={4} height={18} rx={2} fill="#CFE5FF" opacity={0.6} />
        <Path d="M13 50 L35 50 L33 56 L15 56 Z" fill="#CFE5FF" opacity={0.7} />
        <Rect x={11} y={4} width={5} height={3} rx={1.5} fill="#FFE08A" />
        <Rect x={32} y={4} width={5} height={3} rx={1.5} fill="#FFE08A" />
        <Rect x={11} y={55} width={5} height={3} rx={1.5} fill="#FF5A5A" />
        <Rect x={32} y={55} width={5} height={3} rx={1.5} fill="#FF5A5A" />
    </Svg>
);

/** XL — wide boxy SUV with roof rails (purple). icon: bus-outline */
export const XLCar: React.FC<VehicleSvgProps> = ({ size = 44, color = '#7C4DDB' }) => (
    <Svg width={size} height={size} viewBox={VIEWBOX}>
        <Rect x={8} y={8} width={34} height={52} rx={7} fill="rgba(0,0,0,0.18)" />
        <Wheels left={4} right={39} frontY={16} rearY={41} w={6} h={12} />
        <Rect x={7} y={5} width={34} height={52} rx={7} fill={color} stroke="rgba(0,0,0,0.22)" strokeWidth={1} />
        {/* roof rails */}
        <Rect x={12} y={9} width={3} height={44} rx={1.5} fill="rgba(0,0,0,0.28)" />
        <Rect x={33} y={9} width={3} height={44} rx={1.5} fill="rgba(0,0,0,0.28)" />
        <Path d="M15 19 L33 19 L31 10 L17 10 Z" fill="#E0D2FB" opacity={0.92} />
        <Rect x={16} y={22} width={16} height={20} rx={4} fill="#ffffff" opacity={0.14} />
        <Path d="M16 45 L32 45 L30 53 L18 53 Z" fill="#E0D2FB" opacity={0.7} />
        <Rect x={10} y={6} width={6} height={3} rx={1.5} fill="#FFE08A" />
        <Rect x={32} y={6} width={6} height={3} rx={1.5} fill="#FFE08A" />
        <Rect x={10} y={53} width={6} height={3} rx={1.5} fill="#FF5A5A" />
        <Rect x={32} y={53} width={6} height={3} rx={1.5} fill="#FF5A5A" />
    </Svg>
);

const TIER_COMPONENTS: Record<VehicleTier, React.FC<VehicleSvgProps>> = {
    economy: EconomyCar,
    premium: PremiumCar,
    van: VanCar,
    xl: XLCar,
};

/**
 * Map a backend `icon` value or human name onto a tier.
 * Falls back to 'economy' for anything we don't recognise so the map always
 * shows *a* car rather than nothing.
 */
export function resolveVehicleTier(type?: string | null): VehicleTier {
    const t = (type ?? '').toLowerCase().trim();
    if (!t) return 'economy';
    if (t.includes('premium') || t.includes('sport') || t.includes('lux') || t.includes('comfort')) return 'premium';
    if (t.includes('van') || t === 'bus' || t.includes('minivan')) return 'van';
    if (t.includes('xl') || t.includes('suv') || t.includes('bus-outline') || t.includes('large')) return 'xl';
    // 'car-compact', 'economy', 'sedan', 'standard', everything else
    return 'economy';
}

interface VehicleMarkerProps extends VehicleSvgProps {
    /** Backend icon (car-compact / car-sport / bus / bus-outline) or tier name. */
    type?: string | null;
}

/**
 * Tier-aware car marker. Pass the ride's vehicle-type `icon` or `name` and it
 * renders the matching silhouette. Use this inside CarMarker / a Marker child.
 */
export const VehicleMarker: React.FC<VehicleMarkerProps> = ({ type, size = 44, color }) => {
    const Car = TIER_COMPONENTS[resolveVehicleTier(type)];
    return <Car size={size} color={color} />;
};

export default VehicleMarker;
