import React from 'react';
import Svg, { Rect, Path, G } from 'react-native-svg';

/**
 * Top-down (bird's-eye) car SVG markers, one silhouette per vehicle tier.
 *
 * Lyft-style look: a WHITE body with a near-black windshield at the front, a
 * white roof with faint panel seams, a smaller dark rear window, a thin red
 * taillight strip at the rear, small wheel/mirror nubs and a soft drop shadow.
 * The big front windshield vs. the small rear window + red tail makes heading
 * obvious as the Marker rotates — no glow/pill needed.
 *
 * Tiers are told apart by SILHOUETTE so they read at map size:
 *   - Sedans (Economy / Premium) use a slender capsule: domed nose, straight
 *     sides, rounded tail.
 *   - Van / XL use a wide straight-sided BOX with squared corners and bigger
 *     wheels; XL is the widest and carries roof rails.
 *
 * Drawn nose-up (north) on a 48×64 canvas so `rotation={heading}` on the
 * Marker points it the right way. No gradients/filters — keeps the Android
 * Marker snapshot cheap. Mirrors backend/seed_vehicle_types.py;
 * `resolveVehicleTier()` maps the backend `icon` or human name onto a
 * silhouette, falling back to the economy sedan.
 */

export type VehicleTier = 'economy' | 'premium' | 'van' | 'xl';

// Shared white-and-red palette.
const BODY = '#F7F8FA';
const EDGE = 'rgba(0,0,0,0.16)';
const GLASS = '#191A20'; // dark windshield / rear window
const SEAM = '#D4D7DE'; // roof panel lines
const TAIL = '#E11D2A'; // rear taillight accent
const WHEEL = '#26262C';
const MIRROR = '#E2E4EA';
const RAIL = '#E11D2A';

const f = (n: number): number => Math.round(n * 100) / 100;

interface VehicleSvgProps {
    size?: number;
    /** Override the body colour (e.g. grey-out when a driver is offline). */
    color?: string;
}

interface Geometry {
    shape: 'sedan' | 'box';
    x: number;
    y: number;
    w: number;
    h: number;
    frontRy?: number; // sedan front-dome height
    rearR?: number; // corner radius (sedan tail / box corners)
    rails?: boolean; // SUV roof rails
}

/** Slender sedan capsule: domed front (top), straight sides, rounded rear. */
function sedanPath(g: Geometry): string {
    const { x: bx, y: by, w: bw, h: bh } = g;
    const rx = bw / 2;
    const ry = g.frontRy ?? 12;
    const rr = g.rearR ?? 12;
    const sh = by + ry; // front shoulder
    const rsY = by + bh - rr; // rear shoulder
    return (
        `M${f(bx)} ${f(sh)} A${f(rx)} ${f(ry)} 0 0 1 ${f(bx + bw)} ${f(sh)} ` +
        `L${f(bx + bw)} ${f(rsY)} Q${f(bx + bw)} ${f(by + bh)} ${f(bx + bw - rr)} ${f(by + bh)} ` +
        `L${f(bx + rr)} ${f(by + bh)} Q${f(bx)} ${f(by + bh)} ${f(bx)} ${f(rsY)} Z`
    );
}

/** One reusable top-down car; geometry varies the silhouette per tier. */
const TopDownCar: React.FC<{ g: Geometry; size: number; color?: string }> = ({ g, size, color }) => {
    const body = color ?? BODY;
    const { shape, x: bx, y: by, w: bw, h: bh } = g;
    const cx = bx + bw / 2;
    const isBox = shape === 'box';
    const boxR = g.rearR ?? 6;

    // Glass + accents positioned by fractions of the body box.
    const wsInset = isBox ? 2.5 : 2;
    const rwInset = isBox ? 3 : 3;
    const ww = isBox ? 3.4 : 2.4; // wheel size (boxes get bigger wheels)
    const wh = isBox ? 8 : 6;

    return (
        <Svg width={size} height={size} viewBox="0 0 48 64">
            {/* soft drop shadow (two offset layers for a softer edge) */}
            {isBox ? (
                <>
                    <Rect x={bx + 1.3} y={by + 4} width={bw} height={bh} rx={boxR} fill="rgba(0,0,0,0.09)" />
                    <Rect x={bx + 0.8} y={by + 2.2} width={bw} height={bh} rx={boxR} fill="rgba(0,0,0,0.15)" />
                </>
            ) : (
                <>
                    <Path d={sedanPath(g)} fill="rgba(0,0,0,0.09)" transform="translate(1.3, 4)" />
                    <Path d={sedanPath(g)} fill="rgba(0,0,0,0.15)" transform="translate(0.8, 2.2)" />
                </>
            )}
            {/* wheel nubs (under the body so they peek out) */}
            <G fill={WHEEL}>
                <Rect x={bx - ww * 0.5} y={by + bh * 0.27} width={ww} height={wh} rx={1.1} />
                <Rect x={bx + bw - ww * 0.5} y={by + bh * 0.27} width={ww} height={wh} rx={1.1} />
                <Rect x={bx - ww * 0.5} y={by + bh * 0.71} width={ww} height={wh} rx={1.1} />
                <Rect x={bx + bw - ww * 0.5} y={by + bh * 0.71} width={ww} height={wh} rx={1.1} />
            </G>
            {/* side mirror nubs near the front */}
            <G fill={MIRROR}>
                <Rect x={bx - 2} y={by + bh * 0.205} width={2.6} height={2.4} rx={1} />
                <Rect x={bx + bw - 0.6} y={by + bh * 0.205} width={2.6} height={2.4} rx={1} />
            </G>
            {/* body */}
            {isBox ? (
                <Rect x={bx} y={by} width={bw} height={bh} rx={boxR} fill={body} stroke={EDGE} strokeWidth={1} />
            ) : (
                <Path d={sedanPath(g)} fill={body} stroke={EDGE} strokeWidth={1} />
            )}
            {/* thin red taillight at the rear edge */}
            <Rect x={cx - bw * 0.35} y={by + bh - 3.4} width={bw * 0.7} height={1.8} rx={0.9} fill={TAIL} />
            {/* roof panel seams */}
            <Rect x={bx + 3} y={by + bh * 0.5} width={bw - 6} height={0.8} fill={SEAM} />
            <Rect x={bx + 3} y={by + bh * 0.625} width={bw - 6} height={0.8} fill={SEAM} />
            {/* roof rails (SUV only) */}
            {g.rails && (
                <>
                    <Rect x={bx + 3.5} y={by + bh * 0.18} width={2.5} height={bh * 0.6} rx={1.2} fill={RAIL} opacity={0.85} />
                    <Rect x={bx + bw - 6} y={by + bh * 0.18} width={2.5} height={bh * 0.6} rx={1.2} fill={RAIL} opacity={0.85} />
                </>
            )}
            {/* dark rear window (smaller than the windshield) */}
            <Rect x={bx + rwInset} y={by + bh * 0.7} width={bw - rwInset * 2} height={bh * 0.134} rx={4} fill={GLASS} />
            {/* big dark windshield near the front (white hood sits above it) */}
            <Rect x={bx + wsInset} y={by + bh * 0.15} width={bw - wsInset * 2} height={bh * 0.215} rx={isBox ? 3 : 5} fill={GLASS} />
        </Svg>
    );
};

const GEOMETRY: Record<VehicleTier, Geometry> = {
    economy: { shape: 'sedan', x: 11, y: 4, w: 26, h: 56, frontRy: 12, rearR: 12 },
    premium: { shape: 'sedan', x: 12, y: 3, w: 24, h: 58, frontRy: 11, rearR: 11 },
    van: { shape: 'box', x: 8, y: 4, w: 32, h: 57, rearR: 6 },
    xl: { shape: 'box', x: 6, y: 5, w: 36, h: 54, rearR: 5, rails: true },
};

/** Economy — compact sedan. icon: car-compact */
export const EconomyCar: React.FC<VehicleSvgProps> = ({ size = 44, color }) => (
    <TopDownCar g={GEOMETRY.economy} size={size} color={color} />
);
/** Premium — sleeker sedan. icon: car-sport */
export const PremiumCar: React.FC<VehicleSvgProps> = ({ size = 44, color }) => (
    <TopDownCar g={GEOMETRY.premium} size={size} color={color} />
);
/** Van — long boxy minivan. icon: bus */
export const VanCar: React.FC<VehicleSvgProps> = ({ size = 44, color }) => (
    <TopDownCar g={GEOMETRY.van} size={size} color={color} />
);
/** XL — wide boxy SUV with roof rails. icon: bus-outline */
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
