/**
 * Which app this JS runtime belongs to, recorded by setAppIdentity() at
 * startup. Lets shared auth code scope logout side effects to the app that is
 * signing out: with per-login sessions a dual-role account can be signed in to
 * the rider and driver apps at once. Null until set (tests, older call sites):
 * callers then keep their pre-existing behaviour. No imports: safe in headless
 * tasks.
 */
export type AppSurface = 'rider' | 'driver';

let surface: AppSurface | null = null;

export function setAppSurface(value: AppSurface): void {
  surface = value;
}

export function getAppSurface(): AppSurface | null {
  return surface;
}
