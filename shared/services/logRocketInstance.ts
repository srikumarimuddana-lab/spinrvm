// Shared handle to the LogRocket RN SDK module, set once by each app's root
// `app/_layout.tsx` after its own guarded require (Expo Go / web / Android
// default-off / the EXPO_PUBLIC_ENABLE_LOGROCKET kill flag are all handled
// there — this module only holds the resulting reference so screen-level
// code elsewhere in the app, e.g. useLogRocketPrivacyScreen, doesn't need to
// duplicate that guard or re-require the native module).
let instance: any = null;

export function setLogRocketInstance(lr: any): void {
  instance = lr;
}

export function getLogRocketInstance(): any {
  return instance;
}
