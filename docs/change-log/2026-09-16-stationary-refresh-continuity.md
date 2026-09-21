# Stationary tracking refresh continuity

## Problem and change

The one-minute background self-heal reloads the stationary tracking setting.
A network error or three-second settings timeout previously changed an enabled
setting to false. The self-heal then replaced zero-distance GPS sampling with a
10-metre movement filter, preventing a parked driver from renewing presence.
Retain the last confirmed setting on read failure and persist it in device-only,
lock-screen-readable SecureStore for cold background runtimes;
continue reporting the error. A successful false response still disables it.

## Scope and alternative

Only the driver settings reader changes. Increasing the backend presence timeout
would delay disappearance without fixing lost GPS callbacks. No presence timeout,
dispatch policy, permissions, or location freshness checks change.
Fresh installs still default off until a successful settings read; this change
does not guarantee callbacks when the OS suspends or terminates the application.

## Live configuration

The production settings row and public API were confirmed false during the
investigation. The existing stationary tracking setting was enabled and the
public API subsequently confirmed true. Existing clients must reapply native
options on foreground resume or offline/online. Client delivery of this source
change requires a subsequent driver release or compatible OTA update.

## Verification

Two regression tests failed before the change: refresh rejection and timeout
both disabled enabled tracking. They now retain it, and explicit disable remains
covered. Physical-device background continuity has not yet been verified.

## Rollback

Revert the reader change to restore default-off on refresh failure. The existing
admin stationary tracking switch can explicitly disable zero-distance sampling.
