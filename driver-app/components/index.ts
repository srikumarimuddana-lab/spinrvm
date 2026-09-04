// Export all driver dashboard components
//
// DriverTopBar used to re-export here from a top-level, unused duplicate
// of the real component (driver-app/components/dashboard/DriverTopBar.tsx,
// the one actually rendered, exported via ./dashboard's own barrel) —
// removed 2026-09-04 along with the dead file itself. Nothing imported
// DriverTopBar from this barrel specifically (verified via grep before
// removal); if a future screen needs it, import from './dashboard' instead.

export { RideOfferPanel } from './panels/RideOfferPanel';
