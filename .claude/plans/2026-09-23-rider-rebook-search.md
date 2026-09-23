# Rider rebook and searching screen

Supporting note for the rider booking-funnel fix. Impact and rollback live in
`docs/change-log/2026-09-23-rider-rebook-search.md`.

## Problem statement

Live rider-app testing, September 2026. The first booking works. The trip
after a cancelled search does not.

1. **Where to? reopens the last trip.** The rider taps Where to?, searches,
   and cancels while the app is looking for a driver. They land on home.
   The next Where to? still shows the previous pickup and destination.
   Saved places and recents fill the list. They wanted current location as
   pickup and a new destination. Uber and Lyft start that sheet fresh:
   pickup is current GPS, the destination is empty, and Home, Work, and
   recents are suggestions, not filled-in fields.

2. **The pickup X blanks the screen.** Tapping the X on the From address
   shows a white screen, and sometimes jumps to the next page. The screen
   the rider is on should stay open.

3. **The next booking leaves "Looking for a driver".** If they continue
   with the leftover addresses and confirm Economy, XL, or any other
   vehicle, the searching screen either returns to home or the app crashes.
   Economy and XL are the same path. Neither vehicle type has its own crash.

Riders should stay on Looking for a driver until that ride is matched or
they cancel it. A cancel or "no active ride" result for the previous ride
must not clear the new one.

## Why it happens

`clearRide()` keeps pickup and dropoff on purpose. Wiping that draft inside
`clearRide` previously left the vehicle screen with no pickup, stuck
loading, and bounced the rider home. The destination screen then copied the
kept draft into the inputs. The GPS bind only ran when `userLocation`
changed, so a stored address was never replaced with Current Location.

The pickup X set pickup to null. `confirm-pickup` called `router.back()`
during render and returned nothing. A copy of that screen still mounted
under the destination sheet popped the stack and painted blank.

`createRide` cleared `_clearedRideId`. A late cancel for the old ride, a
home check that still saw the old ride, or `/rides/active` saying there was
no ride yet, cleared the new ride and sent the rider home.

## What we ship

- Where to? or a home shortcut, with no live ride, starts a new trip.
  Pickup becomes Current Location when GPS is already known. Destination
  and stops are empty. Home, Work, and recents stay as tappable rows.
- `resetBookingDraft()` does that. `clearRide()` is unchanged.
- The pickup X stays on the destination screen. With GPS it restores
  Current Location. Without GPS it clears the field. It does not open the
  next screen.
- Confirm pickup goes back only while that screen is focused, and shows a
  loader instead of an empty view.
- `createRide` leaves the previous cancel latch in place. `fetchActiveRide`
  does not clear a local ride whose id differs from that latch. A
  `ride_cancelled` socket event or push leaves the screen only when it is
  for the ride currently on screen.

No backend change, no migration, no wallet or Stripe write.

## Verification

Jest, one process: ride store, cancel-flicker, cleared-ride refetch, cancel
signal, search destination, confirm pickup, home, and the rider socket
hook tests. Not run on a device or as a production build. Rider-app has no
screenshot suite.
