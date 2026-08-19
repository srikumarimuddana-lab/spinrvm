/**
 * Open/close state for the rider Safety panel.
 *
 * Exists because the panel MUST render at the app root, not next to the SOS
 * button. The button lives inside `styles.sosButton` on the home map --
 * `position:'absolute', left:20, bottom:'47%'` inside the map overlay tree --
 * and rendering the sheet's Modal there made it inherit that container's
 * geometry instead of escaping it: the sheet started 20pt from the left and
 * ran off the right edge by the same 20pt, and the container growing to fit
 * the sheet shoved the SOS button up into the status bar.
 *
 * Hoisting the sheet to the root (see components/SafetySheetHost.tsx, mounted
 * in app/_layout.tsx beside ConfirmSheet/Toast) makes that structurally
 * impossible rather than something each of the five call sites has to get
 * right. A store rather than prop-drilling because the button and the sheet
 * now live in different parts of the tree.
 */
import { create } from 'zustand';

interface SafetySheetState {
  visible: boolean;
  /** Undefined when SOS is pressed with no active ride — the sheet handles it. */
  rideId?: string;
  open: (rideId?: string) => void;
  close: () => void;
}

export const useSafetySheetStore = create<SafetySheetState>((set) => ({
  visible: false,
  rideId: undefined,
  open: (rideId) => set({ visible: true, rideId }),
  close: () => set({ visible: false }),
}));
