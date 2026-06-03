import React from 'react';
import Svg, { Rect, Path, G } from 'react-native-svg';

/**
 * Top-down (bird's-eye) car SVG markers, one silhouette per vehicle tier.
 *
 * Lyft-style look: a WHITE body with a domed front and squarer rear, a
 * near-black windshield carrying a glowing RED pill at the front (the brand
 * colour), faint roof panel seams, a dark rear window, a thin red taillight
 * accent at the rear, and small wheel/mirror nubs poking out. A soft drop
 * shadow sits underneath.
 *
 * Every car is drawn nose-up (north) on a 48×64 canvas so `rotation={heading}`
 * on the Marker points it the right way. The glowing pill lives at the FRONT,
 * so heading reads at a glance. No gradients/filters — keeps the Android
 * Marker snapshot cheap.
 *
 * Tiers are told apart by SHAPE, not colour: Economy = compact sedan,
 * Premium = sleeker sedan, Van = long boxy minivan, XL = wide SUV with roof
 * rails. They mirror backend/seed_vehicle_types.py. `resolveVehicleTier()`
 * maps the backend `icon` (car-compact / car-sport / bus / bus-outline) or
 * the human name onto a silhouette, falling back to the economy sedan.
 */

export type VehicleTier = 'economy' | 'premium' | 'van' | 'xl';

// Shared white-and-red palette.
const BODY = '#F6F7F9';
const EDGE = 'rgba(0,0,0,0.14)';
const GLASS = '#1B1C22'; // dark windshield / rear window
const SEAM = '#D6D9DF'; // roof panel lines
const GLOW = '#FF1E4D'; // red brand pill
const GLOW_CORE = '#FF9DB2'; // lighter pill centre
const TAIL = '#E11D2A'; // rear taillight accent
const WHEEL = '#2A2A30';
const MIRROR = '#E2E4EA';
const RAIL = '#E11D2A';

const f = (n: number): number => Math.round(n * 100) / 100;

interface VehicleSvgProps {
    size?: number;
    /** Override the body colour (e.g. grey-out when a driver is offline). */
    color?: string;
}

interface Geometry {
    x: number;
    y: number;
    w: number;
    h: number;
    frontR: number; // front dome height (smaller = flatter/boxier nose)
    rearR: number; // rear corner radius
    rails?: boolean; // SUV roof rails
}

/** One reusable top-down car; geometry varies the silhouette per tier. */
const TopDownCar: React.FC<{ g: Geometry; size: number; color?: string }> = ({ g, size, color }) => {
    const body = color ?? BODY;
    const { x: bx, y: by, w: bw, h: bh, frontR, rearR } = g;
    const cx = bx + bw / 2;
    const shoulder = by + frontR; // where the front dome meets the straight sides
    const rx = bw / 2;

    // Body outline: elliptical domed front, straight sides, rounded rear.
    const bodyPath =
        `M${f(bx)} ${f(shoulder)} ` +
        `A${f(rx)} ${f(frontR)} 0 0 1 ${f(bx + bw)} ${f(shoulder)} ` +
        `L${f(bx + bw)} ${f(by + bh - rearR)} ` +
        `Q${f(bx + bw)} ${f(by + bh)} ${f(bx + bw - rearR)} ${f(by + bh)} ` +
        `L${f(bx + rearR)} ${f(by + bh)} ` +
        `Q${f(bx)} ${f(by + bh)} ${f(bx)} ${f(by + bh - rearR)} Z`;

    // Windshield: follows the dome (inset), down to ~40% of the body.
    const i = 1.5;
    const wsBottom = by + bh * 0.4;
    const wsPath =
        `M${f(bx + i)} ${f(shoulder)} ` +
        `A${f(rx - i)} ${f(frontR - i)} 0 0 1 ${f(bx + bw - i)} ${f(shoulder)} ` +
        `L${f(bx + bw - i)} ${f(wsBottom)} L${f(bx + i)} ${f(wsBottom)} Z`;

    // Glowing red pill in the windshield centre.
    const pw = bw * 0.14;
    const ph = bh * 0.11;
    const pcy = by + bh * 0.31;

    return (
        <Svg width={size} height={size} viewBox="0 0 48 64">
            {/* soft drop shadow (body offset down/right) */}
            <Path d={bodyPath} fill="rgba(0,0,0,0.16)" transform="translate(1, 3)" />
            {/* wheel nubs (drawn under the body so they peek out at the sides) */}
            <G fill={WHEEL}>
                <Rect x={bx - 1.5} y={by + bh * 0.3} width={3} height={7} rx={1.2} />
                <Rect x={bx + bw - 1.5} y={by + bh * 0.3} width={3} height={7} rx={1.2} />
                <Rect x={bx - 1.5} y={by + bh * 0.62} width={3} height={7} rx={1.2} />
                <Rect x={bx + bw - 1.5} y={by + bh * 0.62} width={3} height={7} rx={1.2} />
            </G>
            {/* side mirror nubs near the front */}
            <G fill={MIRROR}>
                <Rect x={bx - 2} y={by + bh * 0.25} width={3} height={2.5} rx={1} />
                <Rect x={bx + bw - 1} y={by + bh * 0.25} width={3} height={2.5} rx={1} />
            </G>
            {/* body */}
            <Path d={bodyPath} fill={body} stroke={EDGE} strokeWidth={1} />
            {/* thin red taillight accent at the rear edge */}
            <Rect x={bx + rearR} y={by + bh - 3.2} width={bw - rearR * 2} height={2.2} rx={1.1} fill={TAIL} />
            {/* roof panel seams */}
            <Rect x={bx + 4} y={by + bh * 0.5} width={bw - 8} height={0.9} fill={SEAM} />
            <Rect x={bx + 4} y={by + bh * 0.62} width={bw - 8} height={0.9} fill={SEAM} />
            {/* dark rear window */}
            <Rect x={bx + 3} y={by + bh * 0.72} width={bw - 6} height={bh * 0.12} rx={3} fill={GLASS} />
            {/* roof rails (SUV only) */}
            {g.rails && (
                <>
                    <Rect x={bx + 3.5} y={shoulder} width={2.5} height={by + bh - rearR - shoulder} rx={1.2} fill={RAIL} opacity={0.85} />
                    <Rect x={bx + bw - 6} y={shoulder} width={2.5} height={by + bh - rearR - shoulder} rx={1.2} fill={RAIL} opacity={0.85} />
                </>
            )}
            {/* dark windshield */}
            <Path d={wsPath} fill={GLASS} />
            {/* glowing red pill: outer halo, body, lighter core */}
            <Rect x={cx - pw} y={pcy - ph * 0.9} width={pw * 2} height={ph * 1.8} rx={pw} fill={GLOW} opacity={0.32} />
            <Rect x={cx - pw / 2} y={pcy - ph / 2} width={pw} height={ph} rx={pw / 2} fill={GLOW} />
            <Rect x={cx - pw * 0.22} y={pcy - ph * 0.28} width={pw * 0.44} height={ph * 0.56} rx={pw * 0.22} fill={GLOW_CORE} />
        </Svg>
    );
};

const GEOMETRY: Record<VehicleTier, Geometry> = {
    economy: { x: 10, y: 6, w: 28, h: 52, frontR: 14, rearR: 7 },
    premium: { x: 11, y: 5, w: 26, h: 54, frontR: 13, rearR: 8 },
    van: { x: 8, y: 4, w: 32, h: 56, frontR: 10, rearR: 5 },
    xl: { x: 7, y: 5, w: 34, h: 52, frontR: 10, rearR: 5, rails: true },
};

/** Economy — compact sedan. icon: car-compact */
export const EconomyCar: React.FC<VehicleSvgProps> = ({ size = 44, color }) => (
    <TopDownCar g={GEOMETRY.economy} size={size} color={color} />
);
/** Premium — sleeker sedan. icon: car-sport */
export const PremiumCar: React.FC<VehicleSvgProps> = ({ size = 44, color }) => (
    <TopDownCar g={GEOMETRY.premium} size={size} color={color} />
);
/** Van — long minivan. icon: bus */
export const VanCar: React.FC<VehicleSvgProps> = ({ size = 44, color }) => (
    <TopDownCar g={GEOMETRY.van} size={size} color={color} />
);
/** XL — wide SUV with roof rails. icon: bus-outline */
export const XLCar: React.FC<VehicleSvgProps> = ({ size = 44, color }) => (
    <TopDownCar g={GEOMETRY.xl} size={size} color={color} />
);

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
    const tier = resolveVehicleTier(type);
    return <TopDownCar g={GEOMETRY[tier]} size={size} color={color} />;
};

export default VehicleMarker;
