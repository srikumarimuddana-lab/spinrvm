/**
 * Human-readable OTA bundle identity for display in Settings/About.
 *
 * OTA updates apply on the SECOND app launch, and nothing on screen said
 * which JS bundle was running — road tests of freshly-shipped fixes were
 * blind (2026-08-28: a tracking test likely ran the previous bundle).
 * This label makes every field test verifiable at a glance.
 */

export function otaVersionLabel(): string {
  try {
    // Lazy require: expo-updates throws in bare/jest environments where the
    // native module is absent; the label degrades instead of crashing.
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const Updates = require('expo-updates');
    if (Updates?.updateId) {
      // Full UUID is noise on screen; the first 8 chars identify a build
      // uniquely for any practical comparison against the EAS dashboard.
      return `OTA ${String(Updates.updateId).slice(0, 8)}`;
    }
    if (Updates?.isEmbeddedLaunch === false && Updates?.updateId == null) {
      return 'OTA unknown';
    }
    return 'embedded build';
  } catch {
    return 'dev build';
  }
}
