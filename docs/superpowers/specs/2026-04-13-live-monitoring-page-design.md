# Live Monitoring Page — Design Spec
**Date:** 2026-04-13  
**Status:** Approved  
**Route:** `/dashboard/monitoring`

---

## Overview

A real-time operations map page for the Spinr admin dashboard. Dispatchers can see all active/inactive drivers and all active rides on a single Google Maps view, updated live via WebSocket. Clicking any marker opens a full detail panel. A live alert feed logs key events.

---

## 1. Page & Route

- **Route:** `/dashboard/monitoring`
- **Sidebar entry:** "Live Monitor" with a radar/pulse icon
- **Rendering:** `"use client"` — no SSR (map + WebSocket require browser APIs)

### Layout (3-zone)
```
┌──────────────────────────────────────────────────────┐
│  TOOLBAR: counters · filters · search · follow toggle │
├─────────────────────────────────┬────────────────────┤
│                                 │                    │
│         GOOGLE MAP              │   DETAIL PANEL     │
│   (drivers + rides + routes)    │  (driver or ride)  │
│                                 │                    │
├─────────────────────────────────┴────────────────────┤
│  ALERT FEED  (collapsible drawer, ~200px tall)        │
└──────────────────────────────────────────────────────┘
```
- Map: ~70% width, detail panel: ~30%
- Detail panel shows empty state ("Select a driver or ride") when nothing selected
- Alert feed starts collapsed; badge on toggle shows unread count

---

## 2. Map Layer (Google Maps JS API)

**Library:** `@react-google-maps/api`  
**API key:** `NEXT_PUBLIC_GOOGLE_MAPS_API_KEY` in `.env.local`  
**Initial center:** Primary service area centroid on first load; respects "follow" mode after.

### Markers

| Marker | Shape | Color | Label |
|---|---|---|---|
| Online driver (no ride) | Circle + car icon | Green | Driver initials |
| Online driver (on ride) | Circle + car icon | Amber | Driver initials |
| Offline driver (if shown) | Circle + car icon | Gray (faded) | Driver initials |
| Active ride pickup | Pin | Blue | "P" |
| Active ride dropoff | Pin | Red | "D" |
| Ride in-progress route | Polyline | Blue dashed | — |

### Interactions
- Click driver marker → driver detail in side panel, map pans to driver
- Click ride pickup/dropoff pin → ride detail in panel
- Follow mode: map continuously re-centers on selected driver as position updates
- Markers animate smoothly to new positions (CSS transition) rather than jumping

### Layer Toggles (controlled by toolbar)
- Online drivers — default: ON
- Offline drivers — default: OFF
- Active ride routes — default: ON

---

## 3. WebSocket & Data Flow

### Initial Load (parallel REST calls on mount)
1. `GET /api/admin/monitoring/drivers` — all drivers with current lat/lng, status, active ride ID
2. `GET /api/admin/monitoring/rides` — all active rides (status: `searching`, `driver_assigned`, `driver_arrived`, `in_progress`) with pickup/dropoff coords, driver and rider info

State stored in `useRef` maps (keyed by ID) for O(1) marker updates — not React state — to avoid re-rendering the full map on every position tick. React state only used for: selected item, filter values, alert feed entries, counters.

### WebSocket Connection
- Endpoint: `ws://[backend]/ws/admin/{uuid}`
- Auth message on connect: `{type: "auth", token: "<admin_jwt>"}`
- Managed by `useMonitoringSocket` custom hook (connect, exponential-backoff reconnect, disconnect on unmount)

### Events Handled

| WS Event | Frontend Action |
|---|---|
| `driver_location_update` | Update driver position in ref map, animate marker |
| `ride_status_changed` | Update ride status; refresh detail panel if that ride is selected |
| `driver_status_changed` | Update driver online/offline marker color; update counters |
| `ride_requested` | Add new ride pins to map; push entry to alert feed |
| `ride_completed` | Remove ride from map; push entry to alert feed |
| `ride_cancelled` | Remove ride from map; push entry to alert feed |

### Backend Changes Required

1. **New REST endpoints:**
   - `GET /api/admin/monitoring/drivers` — returns all drivers with `id`, `name`, `phone`, `photo_url`, `current_lat`, `current_lng`, `is_online`, `is_available`, `vehicle_*`, `rating`, `total_rides`, `active_ride_id`, `service_area_id`, `earnings_today`, `time_online_today`
   - `GET /api/admin/monitoring/rides` — returns active rides with full enriched shape (rider, driver, coords, status, fare, timestamps)

2. **WebSocket broadcast to admin clients:**
   - When a driver sends `driver_location_update`, backend broadcasts it to all connected `admin` WS clients
   - New broadcast events: `driver_status_changed`, `ride_requested`, `ride_completed`, `ride_cancelled`

---

## 4. Detail Panel

### Driver Panel — 3 tabs: Overview | Rides | Documents

**Overview tab:**
- Photo, name, phone, email, online badge + time online today
- Vehicle: make, model, color, license plate, vehicle type
- Rating (stars), total rides, earnings today
- Service area assignment
- Current ride (if any) — clickable, switches panel to that ride
- Actions: `Contact` (phone dialer), `Flag Driver`, `View Full Profile` → `/dashboard/drivers`

**Rides tab:**
- Last 10 rides: date, pickup→dropoff, fare, status badge
- Scrollable list; each row clickable to load ride detail in panel

**Documents tab:**
- Required docs with status badges: Verified / Expired / Pending
- Quick link to full document review page

### Ride Panel

- Ride ID (copyable), status badge, corporate vs regular tag
- Rider: name, phone, avatar
- Driver: name, phone, avatar — clickable to switch panel to driver
- Pickup address + dropoff address
- Fare, distance km, duration so far, ETA to dropoff
- Status timeline with timestamps: `Requested → Assigned → Driver Arrived → In Progress`
- Actions: `Cancel Ride`, `Contact Rider`, `Contact Driver`

### Empty State
Centered icon + "Select a driver or ride on the map to view details"

---

## 5. Toolbar

```
[🟢 12 Online] [🟡 4 On Ride] [⚫ 8 Offline] [🚗 3 Active Rides]  |
[Area ▾]  [Vehicle Type ▾]  [Search: driver name or ride ID 🔍]  |  [Follow OFF/ON]
```

- Live counters update from WS events (no re-fetch)
- Area + Vehicle Type dropdowns filter visible markers
- Search: driver name highlights marker + opens panel; ride ID does same for rides
- Follow toggle: when ON + driver selected, map auto-centers on them continuously

---

## 6. Alert Feed

- **Default state:** collapsed — pill button shows `⚡ Live Events [N new]`
- **Expanded:** ~200px tall scrollable reverse-chronological list
- **Entry format:** `[HH:MM:SS]  icon  message`
  - 🔴 Driver went offline
  - 🟢 New ride requested
  - ✅ Ride completed (with fare)
  - ❌ Ride cancelled
- Clicking entry: closes drawer, pans map to driver/ride, opens detail panel
- "Clear all" button top-right
- Unread badge resets when drawer opened

---

## 7. New Files

| File | Purpose |
|---|---|
| `src/app/dashboard/monitoring/page.tsx` | Page shell, layout, data orchestration |
| `src/app/dashboard/monitoring/monitoring-map.tsx` | Google Maps component, marker management |
| `src/app/dashboard/monitoring/driver-panel.tsx` | Driver detail panel (3 tabs) |
| `src/app/dashboard/monitoring/ride-panel.tsx` | Ride detail panel |
| `src/app/dashboard/monitoring/toolbar.tsx` | Counters, filters, search, follow toggle |
| `src/app/dashboard/monitoring/alert-feed.tsx` | Collapsible event log drawer |
| `src/hooks/use-monitoring-socket.ts` | WS lifecycle hook |
| `backend/routes/admin/monitoring.py` | New REST endpoints |

### Modified Files
| File | Change |
|---|---|
| `backend/routes/websocket.py` | Broadcast location/status events to admin WS clients |
| `backend/server.py` | Register monitoring router |
| `src/lib/api.ts` | Add `getMonitoringDrivers`, `getMonitoringRides` |
| `src/components/sidebar.tsx` | Add "Live Monitor" nav entry |
| `admin-dashboard/.env.local` | Add `NEXT_PUBLIC_GOOGLE_MAPS_API_KEY` |

---

## 8. Out of Scope

- Historical playback of driver routes
- Dispatcher-to-driver chat from this page
- Mobile/responsive layout (desktop ops tool only)
- Push notifications (covered by alert feed)
