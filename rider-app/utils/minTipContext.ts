import React from 'react';

/**
 * app_settings.min_tip_amount via GET /settings (migration 438): the smallest
 * non-zero tip, 0 meaning no minimum. Provided by app/_layout.tsx, read by the
 * tip screen (app/ride-completed.tsx). Defaults to $1 — the server's own
 * default — so a slow/failed settings fetch still blocks the sub-$1 custom tips
 * the server would reject anyway (backend/utils/tip_policy.py).
 *
 * Lives outside app/_layout.tsx so a screen can read it without importing the
 * whole root layout (and its module-level side effects).
 */
export const MinTipAmountContext = React.createContext<number>(1);
