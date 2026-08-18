/**
 * The head unit's day/night state, as a store the surface can read.
 *
 * Android Auto decides this, not us — it follows the car's own headlight/ambient
 * state, so it flips mid-drive at dusk. Two channels deliver it and we need both:
 *
 *   1. `colorScheme` in the React root's initial props. iternio's
 *      VirtualRenderer.kt:248 puts it there (`context.isDarkMode`) and
 *      MapTemplate.ts:199 passes props straight to our component. That covers
 *      the FIRST render.
 *   2. `onAppearanceDidChange` on the MapTemplate config (MapTemplate.ts:75),
 *      which fires on every subsequent change. That callback lives in
 *      register.ts, outside the React tree — hence this store, exactly like
 *      carMapCamera and carSurfaceGeneration.
 *
 * Reading only the initial prop would leave a driver who set off in daylight
 * staring at a white map at 10pm.
 */
import { create } from 'zustand';

export type CarColorScheme = 'dark' | 'light';

interface CarColorSchemeState {
  scheme: CarColorScheme;
  setScheme: (scheme: CarColorScheme) => void;
}

export const useCarColorScheme = create<CarColorSchemeState>((set) => ({
  // Dark is the safer default: a head unit that never reports is more likely to
  // be a dark cabin than a bright one, and a too-dark map is readable where a
  // too-bright one at night is genuinely dangerous.
  scheme: 'dark',
  setScheme: (scheme) => set((s) => (s.scheme === scheme ? s : { scheme })),
}));

/** Callable from outside React (register.ts's onAppearanceDidChange). */
export const setCarColorScheme = (scheme: CarColorScheme): void => {
  try {
    useCarColorScheme.getState().setScheme(scheme);
  } catch {
    // Never let a theme update take the surface down.
  }
};

/**
 * Night map style for react-native-maps (`customMapStyle`).
 *
 * Deliberately short: each entry is parsed and applied per tile, and a driver
 * needs the road geometry and labels, not styled points of interest. Roads stay
 * lighter than their surroundings so the route line still reads against them.
 */
export const NIGHT_MAP_STYLE: {
  featureType?: string;
  elementType?: string;
  stylers: Record<string, string>[];
}[] = [
  { elementType: 'geometry', stylers: [{ color: '#1B1B20' }] },
  { elementType: 'labels.text.fill', stylers: [{ color: '#9AA0A6' }] },
  { elementType: 'labels.text.stroke', stylers: [{ color: '#1B1B20' }] },
  { featureType: 'poi', stylers: [{ visibility: 'off' }] },
  { featureType: 'transit', stylers: [{ visibility: 'off' }] },
  { featureType: 'road', elementType: 'geometry', stylers: [{ color: '#2A2A31' }] },
  { featureType: 'road.arterial', elementType: 'geometry', stylers: [{ color: '#33333C' }] },
  { featureType: 'road.highway', elementType: 'geometry', stylers: [{ color: '#3E3E49' }] },
  { featureType: 'road', elementType: 'labels.text.fill', stylers: [{ color: '#B9BEC5' }] },
  { featureType: 'water', elementType: 'geometry', stylers: [{ color: '#12161C' }] },
];
