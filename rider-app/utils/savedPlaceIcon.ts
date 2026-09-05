import type { ComponentProps } from 'react';
import type { Ionicons } from '@expo/vector-icons';

export interface SavedPlaceTypeConfig {
  key: string;
  icon: ComponentProps<typeof Ionicons>['name'];
  color: string;
  bg: string;
}

// Rider-facing "type" chips shown when saving a place (saved-places.tsx's
// add form). `icon` here is the actual Ionicons glyph — distinct from the
// `icon` field persisted on a SavedAddress row, which stores the lowercased
// `key` instead (e.g. "work", not "briefcase" — see handleSave below).
export const SAVED_PLACE_TYPES: SavedPlaceTypeConfig[] = [
  { key: 'Home', icon: 'home', color: '#FF3B30', bg: '#FEF2F2' },
  { key: 'Work', icon: 'briefcase', color: '#3B82F6', bg: '#DBEAFE' },
  { key: 'Gym', icon: 'fitness', color: '#10B981', bg: '#ECFDF5' },
  { key: 'School', icon: 'school', color: '#F59E0B', bg: '#FEF3C7' },
  { key: 'Other', icon: 'star', color: '#8B5CF6', bg: '#EDE9FE' },
];

/**
 * Resolve a saved address's display icon/color/background.
 *
 * Prefers the address's own persisted `icon` field (set at save time from
 * the type the rider picked — saved-places.tsx's handleSave sends
 * `icon: selectedType.toLowerCase()`) over re-deriving it from the display
 * name. A plain substring match on the name breaks the moment a rider
 * renames the address to something that doesn't contain the type keyword
 * (types "Gym" but names it "Downtown Fitness Club" — no match on "gym").
 *
 * Falls back to that legacy name-substring heuristic when `icon` is
 * missing or doesn't match a known key — covering addresses saved before
 * this field was reliably read, and rows created by the backend's legacy
 * CSV import (`saved_address_import_service.py`'s `_TYPE_ICONS`), which
 * uses its own default of `"location"` for anything that isn't literally
 * "home" or "work" — a value with no corresponding entry here, so it
 * correctly falls through to the name check and then to "Other".
 */
export function savedPlaceConfig(addr: { name?: string | null; icon?: string | null }): SavedPlaceTypeConfig {
  if (addr.icon) {
    const byIcon = SAVED_PLACE_TYPES.find((t) => t.key.toLowerCase() === addr.icon!.toLowerCase());
    if (byIcon) return byIcon;
  }
  const lower = addr.name?.toLowerCase() || '';
  return (
    SAVED_PLACE_TYPES.find((t) => lower.includes(t.key.toLowerCase())) ||
    SAVED_PLACE_TYPES[SAVED_PLACE_TYPES.length - 1]
  );
}
