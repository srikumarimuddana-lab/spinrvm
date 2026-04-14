# Live Monitoring Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a real-time `/dashboard/monitoring` page that shows all drivers and active rides on a Google Maps view, updated live via WebSocket, with a full-ops detail panel and collapsible alert feed.

**Architecture:** Admin dashboard connects to the backend WebSocket as `client_type="admin"`. The backend broadcasts driver location updates and ride status changes to all connected admin clients. Initial state loads from two new REST endpoints; subsequent updates arrive via WS and are applied imperatively to Google Maps markers (via refs) to avoid React re-renders on every GPS tick.

**Tech Stack:** Next.js 16 (App Router, `"use client"`), `@react-google-maps/api`, FastAPI + Supabase (backend), `socket_manager.ConnectionManager` (WS), Tailwind CSS + shadcn/ui

---

## File Map

**Create:**
- `backend/routes/admin/monitoring.py` — REST endpoints: GET /drivers, GET /rides
- `src/app/dashboard/monitoring/page.tsx` — page shell, state orchestration
- `src/app/dashboard/monitoring/monitoring-map.tsx` — Google Maps imperative marker management
- `src/app/dashboard/monitoring/driver-panel.tsx` — 3-tab driver detail panel
- `src/app/dashboard/monitoring/ride-panel.tsx` — ride detail panel
- `src/app/dashboard/monitoring/toolbar.tsx` — counters, filters, search, follow toggle
- `src/app/dashboard/monitoring/alert-feed.tsx` — collapsible event log drawer
- `src/app/dashboard/monitoring/types.ts` — shared TypeScript types
- `src/hooks/use-monitoring-socket.ts` — WS lifecycle hook

**Modify:**
- `backend/socket_manager.py` — add `broadcast_to_admins()` method
- `backend/routes/websocket.py` — broadcast driver location + status events to admin clients; allow admin client_type
- `backend/server.py` — register monitoring router at `/api`
- `src/lib/api.ts` — add `getMonitoringDrivers`, `getMonitoringRides`
- `src/components/sidebar.tsx` — add "Live Monitor" nav entry
- `admin-dashboard/next.config.ts` — add `/ws/:path*` rewrite for WS proxy
- `admin-dashboard/.env.local` — add `NEXT_PUBLIC_GOOGLE_MAPS_API_KEY`

---

## Task 1: Backend — Monitoring REST Endpoints

**Files:**
- Create: `backend/routes/admin/monitoring.py`

- [ ] **Step 1: Create the monitoring router**

```python
# backend/routes/admin/monitoring.py
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

try:
    from ...db_supabase import _rows_from_res, run_sync
    from ...dependencies import get_admin_user
    from ...supabase_client import supabase
except ImportError:
    from db_supabase import _rows_from_res, run_sync
    from dependencies import get_admin_user
    from supabase_client import supabase

router = APIRouter(prefix="/admin/monitoring", tags=["Monitoring"])

ACTIVE_RIDE_STATUSES = ["searching", "driver_assigned", "driver_arrived", "in_progress"]
ON_RIDE_STATUSES = ["driver_assigned", "driver_arrived", "in_progress"]


@router.get("/drivers")
async def get_monitoring_drivers(current_admin: dict = Depends(get_admin_user)) -> List[Dict[str, Any]]:
    """Return all drivers with current location and status for the live map."""
    drivers_res = await run_sync(
        lambda: supabase.table("drivers")
        .select(
            "id, user_id, is_online, is_available, lat, lng, "
            "vehicle_make, vehicle_model, vehicle_color, license_plate, "
            "vehicle_type_id, rating, total_rides, service_area_id"
        )
        .execute()
    )
    drivers = _rows_from_res(drivers_res)
    if not drivers:
        return []

    user_ids = [d["user_id"] for d in drivers if d.get("user_id")]
    users_res = await run_sync(
        lambda: supabase.table("users")
        .select("id, first_name, last_name, phone, photo_url")
        .in_("id", user_ids)
        .execute()
    )
    users_by_id = {u["id"]: u for u in _rows_from_res(users_res)}

    driver_ids = [d["id"] for d in drivers]
    rides_res = await run_sync(
        lambda: supabase.table("rides")
        .select("id, driver_id")
        .in_("status", ON_RIDE_STATUSES)
        .in_("driver_id", driver_ids)
        .execute()
    )
    active_ride_by_driver = {r["driver_id"]: r["id"] for r in _rows_from_res(rides_res)}

    result = []
    for d in drivers:
        user = users_by_id.get(d.get("user_id", ""), {})
        first = user.get("first_name") or ""
        last = user.get("last_name") or ""
        result.append(
            {
                "id": d["id"],
                "name": f"{first} {last}".strip() or "Unknown Driver",
                "phone": user.get("phone", ""),
                "photo_url": user.get("photo_url"),
                "lat": d.get("lat"),
                "lng": d.get("lng"),
                "is_online": bool(d.get("is_online")),
                "is_available": bool(d.get("is_available")),
                "vehicle_make": d.get("vehicle_make"),
                "vehicle_model": d.get("vehicle_model"),
                "vehicle_color": d.get("vehicle_color"),
                "license_plate": d.get("license_plate"),
                "vehicle_type_id": d.get("vehicle_type_id"),
                "rating": d.get("rating"),
                "total_rides": d.get("total_rides") or 0,
                "active_ride_id": active_ride_by_driver.get(d["id"]),
                "service_area_id": d.get("service_area_id"),
            }
        )
    return result


@router.get("/rides")
async def get_monitoring_rides(current_admin: dict = Depends(get_admin_user)) -> List[Dict[str, Any]]:
    """Return all active rides with rider/driver info for the live map."""
    rides_res = await run_sync(
        lambda: supabase.table("rides")
        .select(
            "id, status, rider_id, driver_id, "
            "pickup_lat, pickup_lng, pickup_address, "
            "dropoff_lat, dropoff_lng, dropoff_address, "
            "driver_current_lat, driver_current_lng, "
            "total_fare, distance_km, created_at, corporate_account_id"
        )
        .in_("status", ACTIVE_RIDE_STATUSES)
        .execute()
    )
    rides = _rows_from_res(rides_res)
    if not rides:
        return []

    rider_ids = list({r["rider_id"] for r in rides if r.get("rider_id")})
    driver_ids = list({r["driver_id"] for r in rides if r.get("driver_id")})

    riders_res = await run_sync(
        lambda: supabase.table("users")
        .select("id, first_name, last_name, phone, photo_url")
        .in_("id", rider_ids)
        .execute()
    )
    riders_by_id = {u["id"]: u for u in _rows_from_res(riders_res)}

    drivers_map_res = await run_sync(
        lambda: supabase.table("drivers")
        .select("id, user_id, lat, lng")
        .in_("id", driver_ids)
        .execute()
    )
    drivers_rows = _rows_from_res(drivers_map_res)
    drivers_by_id = {d["id"]: d for d in drivers_rows}

    driver_user_ids = [d["user_id"] for d in drivers_rows if d.get("user_id")]
    driver_users_res = await run_sync(
        lambda: supabase.table("users")
        .select("id, first_name, last_name, phone")
        .in_("id", driver_user_ids)
        .execute()
    )
    driver_users_by_id = {u["id"]: u for u in _rows_from_res(driver_users_res)}

    result = []
    for r in rides:
        rider = riders_by_id.get(r.get("rider_id", ""), {})
        drv_row = drivers_by_id.get(r.get("driver_id", ""), {})
        drv_user = driver_users_by_id.get(drv_row.get("user_id", ""), {})
        created = r.get("created_at", "")
        result.append(
            {
                "id": r["id"],
                "status": r["status"],
                "rider_id": r.get("rider_id"),
                "rider_name": f"{rider.get('first_name', '')} {rider.get('last_name', '')}".strip() or "Unknown",
                "rider_phone": rider.get("phone"),
                "rider_photo": rider.get("photo_url"),
                "driver_id": r.get("driver_id"),
                "driver_name": f"{drv_user.get('first_name', '')} {drv_user.get('last_name', '')}".strip() or None,
                "driver_phone": drv_user.get("phone"),
                "pickup_lat": r.get("pickup_lat"),
                "pickup_lng": r.get("pickup_lng"),
                "pickup_address": r.get("pickup_address"),
                "dropoff_lat": r.get("dropoff_lat"),
                "dropoff_lng": r.get("dropoff_lng"),
                "dropoff_address": r.get("dropoff_address"),
                "driver_lat": r.get("driver_current_lat") or drv_row.get("lat"),
                "driver_lng": r.get("driver_current_lng") or drv_row.get("lng"),
                "total_fare": r.get("total_fare"),
                "distance_km": r.get("distance_km"),
                "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created),
                "is_corporate": bool(r.get("corporate_account_id")),
            }
        )
    return result
```

- [ ] **Step 2: Verify endpoints respond (manual curl test)**

Start backend, then:
```bash
TOKEN=$(curl -s http://127.0.0.1:8400/api/admin/auth/login \
  -X POST -H "Content-Type: application/json" \
  -d '{"email":"admin@spinr.ca","password":"Admin12345"}' \
  | grep -o '"token":"[^"]*"' | cut -d'"' -f4)

curl -s http://127.0.0.1:8400/api/admin/monitoring/drivers \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool | head -40

curl -s http://127.0.0.1:8400/api/admin/monitoring/rides \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool | head -40
```
Expected: JSON arrays (may be empty if no drivers/rides in dev).

- [ ] **Step 3: Commit**

```bash
git add backend/routes/admin/monitoring.py
git commit -m "feat(backend): add monitoring REST endpoints for drivers and rides"
```

---

## Task 2: Backend — Register Router + WebSocket Admin Broadcast

**Files:**
- Modify: `backend/server.py`
- Modify: `backend/socket_manager.py`
- Modify: `backend/routes/websocket.py`

- [ ] **Step 1: Add `broadcast_to_admins` to ConnectionManager**

In `backend/socket_manager.py`, add this method inside the `ConnectionManager` class after the existing `broadcast` method (around line 55):

```python
    async def broadcast_to_admins(self, message: dict):
        """Broadcast a message to all connected admin WebSocket clients."""
        admin_keys = [k for k in self.active_connections if k.startswith("admin_")]
        for key in admin_keys:
            try:
                await self.active_connections[key].send_json(message)
            except Exception as e:
                logger.warning(f"Failed to send to admin {key}: {e}")
```

- [ ] **Step 2: Register monitoring router in server.py**

In `backend/server.py`, add the import after the existing admin imports (around line 17):

```python
from routes.admin.monitoring import router as monitoring_router
```

Then add the router registration after line 83 (`app.include_router(files_router, prefix="/api")`):

```python
app.include_router(monitoring_router, prefix="/api")
```

- [ ] **Step 3: Allow admin client_type and broadcast location updates in websocket.py**

In `backend/routes/websocket.py`, the block starting at line 101 checks `if client_type == "driver"`. Add an `elif` for admin clients so they can authenticate without a driver profile. Replace the block at lines 100-107:

```python
        # If connecting as driver, ensure user has a driver profile
        if client_type == "driver":
            driver_profile = await db.drivers.find_one({"user_id": user["id"]})
            if not driver_profile:
                await websocket.send_json({"type": "error", "message": "user_is_not_a_driver"})
                await websocket.close()
                return
        elif client_type == "admin":
            # Admin clients must have admin or super_admin role
            if user.get("role") not in ("admin", "super_admin"):
                await websocket.send_json({"type": "error", "message": "admin_access_required"})
                await websocket.close()
                return
```

- [ ] **Step 4: Broadcast location updates to admin clients**

In `websocket.py`, inside the `driver_location` handler, after the loop that forwards to riders (after line 200), add the admin broadcast:

```python
                    # Broadcast live location to all connected admin monitoring clients
                    await manager.broadcast_to_admins(
                        {
                            "type": "driver_location_update",
                            "driver_id": driver_id,
                            "lat": lat,
                            "lng": lng,
                            "speed": data.get("speed"),
                            "heading": data.get("heading"),
                        }
                    )
```

- [ ] **Step 5: Broadcast ride status changes to admin clients**

In `websocket.py`, inside the `ride_status_update` handler (around line 242), after the `send_personal_message` call to the rider, add:

```python
                        # Broadcast to admin monitoring clients
                        await manager.broadcast_to_admins(
                            {
                                "type": "ride_status_changed",
                                "ride_id": ride_id,
                                "status": status,
                            }
                        )
```

- [ ] **Step 6: Broadcast driver connect/disconnect status to admins**

In `websocket.py`, after the `manager.connect` call (line 110), add:

```python
        # Notify admins that a driver came online
        if client_type == "driver":
            driver_profile_for_status = await db.drivers.find_one({"user_id": user["id"]})
            if driver_profile_for_status:
                await manager.broadcast_to_admins(
                    {
                        "type": "driver_status_changed",
                        "driver_id": driver_profile_for_status["id"],
                        "is_online": True,
                    }
                )
```

And inside the `except WebSocketDisconnect` block (line 305), before the existing `manager.disconnect` call:

```python
    except WebSocketDisconnect:
        if connection_key and connection_key.startswith("driver_"):
            # Notify admins the driver went offline
            driver_profile_off = await db.drivers.find_one({"user_id": user["id"]}) if user else None
            if driver_profile_off:
                await manager.broadcast_to_admins(
                    {
                        "type": "driver_status_changed",
                        "driver_id": driver_profile_off["id"],
                        "is_online": False,
                    }
                )
        if connection_key:
            manager.disconnect(connection_key)
```

- [ ] **Step 7: Restart backend and verify no startup errors**

```bash
cd backend
python -m uvicorn server:app --host 127.0.0.1 --port 8400 --reload
```
Expected: Server starts without import errors. Check logs.

- [ ] **Step 8: Commit**

```bash
git add backend/socket_manager.py backend/routes/websocket.py backend/server.py
git commit -m "feat(backend): broadcast driver location/status to admin WS clients"
```

---

## Task 3: Frontend — Dependencies, Env Vars, Config, WS Rewrite

**Files:**
- Modify: `admin-dashboard/next.config.ts`
- Modify: `admin-dashboard/.env.local`

- [ ] **Step 1: Install Google Maps React library**

```bash
cd admin-dashboard
npm install @react-google-maps/api
```
Expected: Package added to `package.json`.

- [ ] **Step 2: Add WS proxy rewrite to next.config.ts**

Replace the full contents of `admin-dashboard/next.config.ts`:

```typescript
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8400/api/:path*",
      },
      {
        // Proxy WebSocket upgrade requests so the browser connects to
        // ws://localhost:3000/ws/... which Next.js forwards to the backend.
        source: "/ws/:path*",
        destination: "http://127.0.0.1:8400/ws/:path*",
      },
    ];
  },
};

export default nextConfig;
```

- [ ] **Step 3: Add env vars to .env.local**

Append to `admin-dashboard/.env.local`:

```
# Google Maps JS API key (required for /dashboard/monitoring map)
# Get one at https://console.cloud.google.com → Maps JavaScript API
NEXT_PUBLIC_GOOGLE_MAPS_API_KEY=

# WebSocket URL override — leave empty in dev (uses Next.js WS proxy above)
# Set to wss://your-backend.com in production
NEXT_PUBLIC_WS_URL=
```

- [ ] **Step 4: Commit**

```bash
git add admin-dashboard/next.config.ts admin-dashboard/.env.local
cd admin-dashboard && git add package.json package-lock.json
git commit -m "feat(frontend): add Google Maps dep and WS proxy rewrite"
```

---

## Task 4: Frontend — Shared Types + API Functions

**Files:**
- Create: `src/app/dashboard/monitoring/types.ts`
- Modify: `src/lib/api.ts`

- [ ] **Step 1: Create shared types file**

```typescript
// src/app/dashboard/monitoring/types.ts

export interface MonitoringDriver {
  id: string;
  name: string;
  phone: string;
  photo_url: string | null;
  lat: number | null;
  lng: number | null;
  is_online: boolean;
  is_available: boolean;
  vehicle_make: string | null;
  vehicle_model: string | null;
  vehicle_color: string | null;
  license_plate: string | null;
  vehicle_type_id: string | null;
  rating: number | null;
  total_rides: number;
  active_ride_id: string | null;
  service_area_id: string | null;
}

export interface MonitoringRide {
  id: string;
  status: "searching" | "driver_assigned" | "driver_arrived" | "in_progress";
  rider_id: string;
  rider_name: string;
  rider_phone: string | null;
  rider_photo: string | null;
  driver_id: string | null;
  driver_name: string | null;
  driver_phone: string | null;
  pickup_lat: number;
  pickup_lng: number;
  pickup_address: string | null;
  dropoff_lat: number;
  dropoff_lng: number;
  dropoff_address: string | null;
  driver_lat: number | null;
  driver_lng: number | null;
  total_fare: number | null;
  distance_km: number | null;
  created_at: string;
  is_corporate: boolean;
}

export type MonitoringWsEvent =
  | { type: "driver_location_update"; driver_id: string; lat: number; lng: number; speed?: number; heading?: number }
  | { type: "ride_status_changed"; ride_id: string; status: string }
  | { type: "driver_status_changed"; driver_id: string; is_online: boolean }
  | { type: "ride_requested"; ride: MonitoringRide }
  | { type: "ride_completed"; ride_id: string; fare?: number }
  | { type: "ride_cancelled"; ride_id: string };

export interface AlertEvent {
  id: string;
  timestamp: string; // ISO
  icon: "online" | "offline" | "ride_new" | "ride_done" | "ride_cancelled";
  message: string;
  driver_id?: string;
  ride_id?: string;
}

export interface MonitoringCounts {
  online: number;
  onRide: number;
  offline: number;
  activeRides: number;
}

export interface MonitoringFilters {
  showOnline: boolean;
  showOffline: boolean;
  showRides: boolean;
  serviceAreaId: string | null;
  vehicleTypeId: string | null;
}

export type SelectedItem =
  | { type: "driver"; id: string }
  | { type: "ride"; id: string }
  | null;
```

- [ ] **Step 2: Add API functions to api.ts**

At the end of `src/lib/api.ts`, add:

```typescript
/* ── Live Monitoring ────────────────────────────── */
export const getMonitoringDrivers = () =>
    request<import("@/app/dashboard/monitoring/types").MonitoringDriver[]>(
        "/api/admin/monitoring/drivers"
    );

export const getMonitoringRides = () =>
    request<import("@/app/dashboard/monitoring/types").MonitoringRide[]>(
        "/api/admin/monitoring/rides"
    );
```

- [ ] **Step 3: Commit**

```bash
git add src/app/dashboard/monitoring/types.ts src/lib/api.ts
git commit -m "feat(frontend): add monitoring types and API functions"
```

---

## Task 5: Frontend — useMonitoringSocket Hook

**Files:**
- Create: `src/hooks/use-monitoring-socket.ts`

- [ ] **Step 1: Create the hook**

```typescript
// src/hooks/use-monitoring-socket.ts
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { MonitoringWsEvent } from "@/app/dashboard/monitoring/types";

type ConnectionStatus = "connecting" | "connected" | "disconnected" | "error";

interface UseMonitoringSocketOptions {
    token: string | null;
    onEvent: (event: MonitoringWsEvent) => void;
}

export function useMonitoringSocket({ token, onEvent }: UseMonitoringSocketOptions) {
    const [status, setStatus] = useState<ConnectionStatus>("disconnected");
    const wsRef = useRef<WebSocket | null>(null);
    const retryCountRef = useRef(0);
    const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const onEventRef = useRef(onEvent);
    onEventRef.current = onEvent; // always up-to-date without recreating the effect

    const clientId = useRef(
        typeof crypto !== "undefined" ? crypto.randomUUID() : Math.random().toString(36).slice(2)
    );

    const connect = useCallback(() => {
        if (!token) return;
        if (wsRef.current?.readyState === WebSocket.OPEN) return;

        const wsBase =
            process.env.NEXT_PUBLIC_WS_URL ||
            (typeof window !== "undefined" ? `ws://${window.location.host}` : "");
        const url = `${wsBase}/ws/admin/${clientId.current}`;

        setStatus("connecting");
        const ws = new WebSocket(url);
        wsRef.current = ws;

        ws.onopen = () => {
            ws.send(JSON.stringify({ type: "auth", token }));
            setStatus("connected");
            retryCountRef.current = 0;
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data) as MonitoringWsEvent & { type: string };
                if (data.type === "ping") {
                    ws.send(JSON.stringify({ type: "pong" }));
                    return;
                }
                // Only forward known monitoring event types
                const knownTypes = [
                    "driver_location_update",
                    "ride_status_changed",
                    "driver_status_changed",
                    "ride_requested",
                    "ride_completed",
                    "ride_cancelled",
                ];
                if (knownTypes.includes(data.type)) {
                    onEventRef.current(data as MonitoringWsEvent);
                }
            } catch {
                // ignore malformed messages
            }
        };

        ws.onerror = () => setStatus("error");

        ws.onclose = () => {
            setStatus("disconnected");
            // Exponential backoff: 1s, 2s, 4s, 8s, max 30s
            const delay = Math.min(1000 * 2 ** retryCountRef.current, 30_000);
            retryCountRef.current += 1;
            retryTimerRef.current = setTimeout(connect, delay);
        };
    }, [token]);

    useEffect(() => {
        connect();
        return () => {
            retryTimerRef.current && clearTimeout(retryTimerRef.current);
            wsRef.current?.close();
        };
    }, [connect]);

    return { status };
}
```

- [ ] **Step 2: Commit**

```bash
git add src/hooks/use-monitoring-socket.ts
git commit -m "feat(frontend): add useMonitoringSocket hook with reconnect backoff"
```

---

## Task 6: Frontend — Toolbar Component

**Files:**
- Create: `src/app/dashboard/monitoring/toolbar.tsx`

- [ ] **Step 1: Create toolbar**

```typescript
// src/app/dashboard/monitoring/toolbar.tsx
"use client";

import { MonitoringCounts, MonitoringFilters } from "./types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Navigation, Search, Wifi, WifiOff } from "lucide-react";

interface ServiceArea { id: string; name: string; }
interface VehicleType { id: string; name: string; }

interface ToolbarProps {
    counts: MonitoringCounts;
    filters: MonitoringFilters;
    onFilterChange: (f: Partial<MonitoringFilters>) => void;
    searchQuery: string;
    onSearchChange: (q: string) => void;
    followMode: boolean;
    onFollowToggle: () => void;
    serviceAreas: ServiceArea[];
    vehicleTypes: VehicleType[];
    wsStatus: "connecting" | "connected" | "disconnected" | "error";
}

export function MonitoringToolbar({
    counts,
    filters,
    onFilterChange,
    searchQuery,
    onSearchChange,
    followMode,
    onFollowToggle,
    serviceAreas,
    vehicleTypes,
    wsStatus,
}: ToolbarProps) {
    return (
        <div className="flex flex-wrap items-center gap-3 border-b border-border bg-background px-4 py-2">
            {/* Live counters */}
            <div className="flex items-center gap-2 text-sm">
                <button
                    onClick={() => onFilterChange({ showOnline: !filters.showOnline })}
                    className={`flex items-center gap-1 rounded px-2 py-1 transition-colors ${
                        filters.showOnline
                            ? "bg-green-500/10 text-green-600 ring-1 ring-green-500/30"
                            : "text-muted-foreground hover:bg-muted"
                    }`}
                >
                    <span className="h-2 w-2 rounded-full bg-green-500" />
                    {counts.online} Online
                </button>
                <button
                    onClick={() => onFilterChange({ showOnline: !filters.showOnline })}
                    className="flex items-center gap-1 rounded bg-amber-500/10 px-2 py-1 text-amber-600 ring-1 ring-amber-500/30"
                >
                    <span className="h-2 w-2 rounded-full bg-amber-500" />
                    {counts.onRide} On Ride
                </button>
                <button
                    onClick={() => onFilterChange({ showOffline: !filters.showOffline })}
                    className={`flex items-center gap-1 rounded px-2 py-1 transition-colors ${
                        filters.showOffline
                            ? "bg-muted text-foreground ring-1 ring-border"
                            : "text-muted-foreground hover:bg-muted"
                    }`}
                >
                    <span className="h-2 w-2 rounded-full bg-gray-400" />
                    {counts.offline} Offline
                </button>
                <button
                    onClick={() => onFilterChange({ showRides: !filters.showRides })}
                    className={`flex items-center gap-1 rounded px-2 py-1 transition-colors ${
                        filters.showRides
                            ? "bg-blue-500/10 text-blue-600 ring-1 ring-blue-500/30"
                            : "text-muted-foreground hover:bg-muted"
                    }`}
                >
                    🚗 {counts.activeRides} Rides
                </button>
            </div>

            <div className="mx-1 h-5 w-px bg-border" />

            {/* Filters */}
            <Select
                value={filters.serviceAreaId ?? "all"}
                onValueChange={(v) =>
                    onFilterChange({ serviceAreaId: v === "all" ? null : v })
                }
            >
                <SelectTrigger className="h-8 w-36 text-xs">
                    <SelectValue placeholder="All Areas" />
                </SelectTrigger>
                <SelectContent>
                    <SelectItem value="all">All Areas</SelectItem>
                    {serviceAreas.map((a) => (
                        <SelectItem key={a.id} value={a.id}>
                            {a.name}
                        </SelectItem>
                    ))}
                </SelectContent>
            </Select>

            <Select
                value={filters.vehicleTypeId ?? "all"}
                onValueChange={(v) =>
                    onFilterChange({ vehicleTypeId: v === "all" ? null : v })
                }
            >
                <SelectTrigger className="h-8 w-36 text-xs">
                    <SelectValue placeholder="All Vehicles" />
                </SelectTrigger>
                <SelectContent>
                    <SelectItem value="all">All Vehicles</SelectItem>
                    {vehicleTypes.map((v) => (
                        <SelectItem key={v.id} value={v.id}>
                            {v.name}
                        </SelectItem>
                    ))}
                </SelectContent>
            </Select>

            <div className="mx-1 h-5 w-px bg-border" />

            {/* Search */}
            <div className="relative">
                <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input
                    value={searchQuery}
                    onChange={(e) => onSearchChange(e.target.value)}
                    placeholder="Driver name or ride ID"
                    className="h-8 w-48 pl-7 text-xs"
                />
            </div>

            <div className="ml-auto flex items-center gap-2">
                {/* WS status indicator */}
                <span className="flex items-center gap-1 text-xs text-muted-foreground">
                    {wsStatus === "connected" ? (
                        <Wifi className="h-3.5 w-3.5 text-green-500" />
                    ) : (
                        <WifiOff className="h-3.5 w-3.5 text-destructive" />
                    )}
                    {wsStatus === "connected" ? "Live" : wsStatus}
                </span>

                {/* Follow mode */}
                <Button
                    size="sm"
                    variant={followMode ? "default" : "outline"}
                    onClick={onFollowToggle}
                    className="h-8 gap-1.5 text-xs"
                >
                    <Navigation className="h-3.5 w-3.5" />
                    Follow {followMode ? "ON" : "OFF"}
                </Button>
            </div>
        </div>
    );
}
```

- [ ] **Step 2: Commit**

```bash
git add src/app/dashboard/monitoring/toolbar.tsx
git commit -m "feat(frontend): add monitoring toolbar with counters, filters, search"
```

---

## Task 7: Frontend — Alert Feed Component

**Files:**
- Create: `src/app/dashboard/monitoring/alert-feed.tsx`

- [ ] **Step 1: Create alert feed**

```typescript
// src/app/dashboard/monitoring/alert-feed.tsx
"use client";

import { useState } from "react";
import { AlertEvent } from "./types";
import { Button } from "@/components/ui/button";
import { ChevronDown, ChevronUp, Zap, X } from "lucide-react";

interface AlertFeedProps {
    events: AlertEvent[];
    onClear: () => void;
    onEventClick: (event: AlertEvent) => void;
}

const ICON_MAP: Record<AlertEvent["icon"], string> = {
    online: "🟢",
    offline: "🔴",
    ride_new: "🟡",
    ride_done: "✅",
    ride_cancelled: "❌",
};

function formatTime(iso: string): string {
    return new Date(iso).toLocaleTimeString("en-CA", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
    });
}

export function AlertFeed({ events, onClear, onEventClick }: AlertFeedProps) {
    const [open, setOpen] = useState(false);
    const [lastSeenCount, setLastSeenCount] = useState(0);

    const unread = events.length - lastSeenCount;

    const handleOpen = () => {
        setOpen(true);
        setLastSeenCount(events.length);
    };

    return (
        <div className="border-t border-border bg-background">
            {/* Toggle pill */}
            <div className="flex items-center justify-between px-4 py-1.5">
                <button
                    onClick={open ? () => setOpen(false) : handleOpen}
                    className="flex items-center gap-2 text-sm font-medium text-foreground"
                >
                    <Zap className="h-4 w-4 text-amber-500" />
                    Live Events
                    {unread > 0 && !open && (
                        <span className="rounded-full bg-destructive px-1.5 py-0.5 text-xs font-bold text-destructive-foreground">
                            {unread}
                        </span>
                    )}
                    {open ? (
                        <ChevronDown className="h-4 w-4" />
                    ) : (
                        <ChevronUp className="h-4 w-4" />
                    )}
                </button>
                {open && events.length > 0 && (
                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={onClear}
                        className="h-6 gap-1 text-xs text-muted-foreground"
                    >
                        <X className="h-3 w-3" /> Clear all
                    </Button>
                )}
            </div>

            {/* Event list */}
            {open && (
                <div className="h-44 overflow-y-auto px-4 pb-2">
                    {events.length === 0 ? (
                        <p className="pt-4 text-center text-xs text-muted-foreground">
                            No events yet
                        </p>
                    ) : (
                        <div className="flex flex-col-reverse gap-0.5">
                            {events.map((evt) => (
                                <button
                                    key={evt.id}
                                    onClick={() => onEventClick(evt)}
                                    className="flex items-center gap-2 rounded px-1 py-0.5 text-left text-xs hover:bg-muted"
                                >
                                    <span className="shrink-0 font-mono text-muted-foreground">
                                        {formatTime(evt.timestamp)}
                                    </span>
                                    <span>{ICON_MAP[evt.icon]}</span>
                                    <span className="truncate">{evt.message}</span>
                                </button>
                            ))}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
```

- [ ] **Step 2: Commit**

```bash
git add src/app/dashboard/monitoring/alert-feed.tsx
git commit -m "feat(frontend): add collapsible alert feed component"
```

---

## Task 8: Frontend — Driver Panel Component

**Files:**
- Create: `src/app/dashboard/monitoring/driver-panel.tsx`

- [ ] **Step 1: Create driver detail panel**

```typescript
// src/app/dashboard/monitoring/driver-panel.tsx
"use client";

import { useState } from "react";
import { MonitoringDriver } from "./types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { ExternalLink, Flag, Phone, Star } from "lucide-react";
import Link from "next/link";

interface DriverPanelProps {
    driver: MonitoringDriver;
    onRideClick: (rideId: string) => void;
}

export function DriverPanel({ driver, onRideClick }: DriverPanelProps) {
    const initials = driver.name
        .split(" ")
        .map((n) => n[0])
        .join("")
        .toUpperCase()
        .slice(0, 2);

    return (
        <div className="flex h-full flex-col overflow-hidden">
            {/* Header */}
            <div className="flex items-center gap-3 border-b border-border p-4">
                <Avatar className="h-12 w-12">
                    <AvatarImage src={driver.photo_url ?? undefined} />
                    <AvatarFallback>{initials}</AvatarFallback>
                </Avatar>
                <div className="min-w-0 flex-1">
                    <p className="truncate font-semibold">{driver.name}</p>
                    <p className="text-xs text-muted-foreground">{driver.phone}</p>
                </div>
                <Badge
                    variant={driver.is_online ? "default" : "secondary"}
                    className={driver.is_online ? "bg-green-500 hover:bg-green-500" : ""}
                >
                    {driver.is_online ? (driver.active_ride_id ? "On Ride" : "Online") : "Offline"}
                </Badge>
            </div>

            <Tabs defaultValue="overview" className="flex flex-1 flex-col overflow-hidden">
                <TabsList className="mx-4 mt-2 w-auto justify-start">
                    <TabsTrigger value="overview" className="text-xs">Overview</TabsTrigger>
                    <TabsTrigger value="rides" className="text-xs">Rides</TabsTrigger>
                    <TabsTrigger value="documents" className="text-xs">Docs</TabsTrigger>
                </TabsList>

                {/* Overview tab */}
                <TabsContent value="overview" className="flex-1 overflow-y-auto px-4 pb-4">
                    {/* Rating + stats */}
                    <div className="mt-3 grid grid-cols-3 gap-2">
                        <div className="rounded-lg bg-muted p-2 text-center">
                            <div className="flex items-center justify-center gap-1 text-lg font-bold">
                                <Star className="h-4 w-4 fill-amber-400 text-amber-400" />
                                {driver.rating?.toFixed(1) ?? "—"}
                            </div>
                            <p className="text-xs text-muted-foreground">Rating</p>
                        </div>
                        <div className="rounded-lg bg-muted p-2 text-center">
                            <p className="text-lg font-bold">{driver.total_rides}</p>
                            <p className="text-xs text-muted-foreground">Rides</p>
                        </div>
                        <div className="rounded-lg bg-muted p-2 text-center">
                            <p className="text-lg font-bold text-green-600">
                                {driver.is_online ? "●" : "○"}
                            </p>
                            <p className="text-xs text-muted-foreground">Status</p>
                        </div>
                    </div>

                    {/* Vehicle info */}
                    <div className="mt-4 space-y-2">
                        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                            Vehicle
                        </p>
                        <div className="rounded-lg border border-border p-3 text-sm">
                            <p className="font-medium">
                                {[driver.vehicle_make, driver.vehicle_model].filter(Boolean).join(" ") || "—"}
                            </p>
                            {driver.vehicle_color && (
                                <p className="text-xs text-muted-foreground">{driver.vehicle_color}</p>
                            )}
                            {driver.license_plate && (
                                <p className="mt-1 font-mono text-xs font-bold uppercase tracking-widest">
                                    {driver.license_plate}
                                </p>
                            )}
                        </div>
                    </div>

                    {/* Current ride */}
                    {driver.active_ride_id && (
                        <div className="mt-4">
                            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                                Current Ride
                            </p>
                            <button
                                onClick={() => onRideClick(driver.active_ride_id!)}
                                className="mt-1 w-full rounded-lg border border-blue-500/30 bg-blue-500/5 px-3 py-2 text-left text-xs text-blue-600 hover:bg-blue-500/10"
                            >
                                Ride #{driver.active_ride_id.slice(-8)} →
                            </button>
                        </div>
                    )}

                    {/* Actions */}
                    <div className="mt-4 flex gap-2">
                        <Button
                            size="sm"
                            variant="outline"
                            className="flex-1 gap-1.5 text-xs"
                            onClick={() => window.open(`tel:${driver.phone}`)}
                        >
                            <Phone className="h-3.5 w-3.5" /> Contact
                        </Button>
                        <Button
                            size="sm"
                            variant="outline"
                            className="flex-1 gap-1.5 text-xs text-destructive hover:text-destructive"
                        >
                            <Flag className="h-3.5 w-3.5" /> Flag
                        </Button>
                    </div>
                    <Link href={`/dashboard/drivers?id=${driver.id}`}>
                        <Button
                            size="sm"
                            variant="ghost"
                            className="mt-2 w-full gap-1.5 text-xs"
                        >
                            <ExternalLink className="h-3.5 w-3.5" /> View Full Profile
                        </Button>
                    </Link>
                </TabsContent>

                {/* Rides tab — placeholder for last 10 rides (requires separate API call) */}
                <TabsContent value="rides" className="flex-1 overflow-y-auto px-4 pb-4">
                    <div className="mt-4">
                        <Link href={`/dashboard/drivers?id=${driver.id}`}>
                            <Button size="sm" variant="outline" className="w-full text-xs">
                                View ride history in Drivers page
                            </Button>
                        </Link>
                    </div>
                </TabsContent>

                {/* Documents tab */}
                <TabsContent value="documents" className="flex-1 overflow-y-auto px-4 pb-4">
                    <div className="mt-4">
                        <Link href={`/dashboard/documents?driver_id=${driver.id}`}>
                            <Button size="sm" variant="outline" className="w-full text-xs">
                                View Documents
                            </Button>
                        </Link>
                    </div>
                </TabsContent>
            </Tabs>
        </div>
    );
}
```

- [ ] **Step 2: Commit**

```bash
git add src/app/dashboard/monitoring/driver-panel.tsx
git commit -m "feat(frontend): add driver detail panel component"
```

---

## Task 9: Frontend — Ride Panel Component

**Files:**
- Create: `src/app/dashboard/monitoring/ride-panel.tsx`

- [ ] **Step 1: Create ride detail panel**

```typescript
// src/app/dashboard/monitoring/ride-panel.tsx
"use client";

import { MonitoringRide } from "./types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Copy, MapPin, Phone, XCircle } from "lucide-react";

interface RidePanelProps {
    ride: MonitoringRide;
    onDriverClick: (driverId: string) => void;
    onCancelRide: (rideId: string) => void;
}

const STATUS_LABELS: Record<string, string> = {
    searching: "Searching",
    driver_assigned: "Driver Assigned",
    driver_arrived: "Driver Arrived",
    in_progress: "In Progress",
};

const STATUS_STEPS = ["searching", "driver_assigned", "driver_arrived", "in_progress"];

const STATUS_COLORS: Record<string, string> = {
    searching: "bg-yellow-500",
    driver_assigned: "bg-blue-500",
    driver_arrived: "bg-purple-500",
    in_progress: "bg-green-500",
};

export function RidePanel({ ride, onDriverClick, onCancelRide }: RidePanelProps) {
    const currentStepIdx = STATUS_STEPS.indexOf(ride.status);
    const elapsed = Math.floor(
        (Date.now() - new Date(ride.created_at).getTime()) / 60_000
    );

    function copyId() {
        navigator.clipboard.writeText(ride.id);
    }

    return (
        <div className="flex h-full flex-col overflow-y-auto">
            {/* Header */}
            <div className="flex items-start justify-between border-b border-border p-4">
                <div>
                    <div className="flex items-center gap-2">
                        <p className="font-mono text-xs text-muted-foreground">
                            #{ride.id.slice(-8)}
                        </p>
                        <button onClick={copyId}>
                            <Copy className="h-3 w-3 text-muted-foreground hover:text-foreground" />
                        </button>
                    </div>
                    <Badge
                        className={`mt-1 text-white ${STATUS_COLORS[ride.status] ?? "bg-gray-500"}`}
                    >
                        {STATUS_LABELS[ride.status] ?? ride.status}
                    </Badge>
                </div>
                {ride.is_corporate && (
                    <Badge variant="outline" className="text-xs">Corporate</Badge>
                )}
            </div>

            <div className="space-y-4 p-4">
                {/* Status timeline */}
                <div>
                    <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        Progress
                    </p>
                    <div className="flex items-center gap-1">
                        {STATUS_STEPS.map((step, i) => (
                            <div key={step} className="flex flex-1 items-center">
                                <div
                                    className={`h-2 w-2 rounded-full ${
                                        i <= currentStepIdx ? "bg-primary" : "bg-muted"
                                    }`}
                                />
                                {i < STATUS_STEPS.length - 1 && (
                                    <div
                                        className={`h-0.5 flex-1 ${
                                            i < currentStepIdx ? "bg-primary" : "bg-muted"
                                        }`}
                                    />
                                )}
                            </div>
                        ))}
                    </div>
                    <div className="mt-1 flex justify-between">
                        {STATUS_STEPS.map((step) => (
                            <p key={step} className="w-16 text-center text-[10px] text-muted-foreground">
                                {STATUS_LABELS[step].split(" ")[0]}
                            </p>
                        ))}
                    </div>
                </div>

                {/* Route */}
                <div className="space-y-2">
                    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        Route
                    </p>
                    <div className="rounded-lg border border-border p-3 text-xs">
                        <div className="flex gap-2">
                            <MapPin className="mt-0.5 h-3.5 w-3.5 shrink-0 text-blue-500" />
                            <p className="text-foreground">
                                {ride.pickup_address ?? `${ride.pickup_lat?.toFixed(4)}, ${ride.pickup_lng?.toFixed(4)}`}
                            </p>
                        </div>
                        <div className="my-1 ml-1.5 h-3 border-l border-dashed border-muted-foreground" />
                        <div className="flex gap-2">
                            <MapPin className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-500" />
                            <p className="text-foreground">
                                {ride.dropoff_address ?? `${ride.dropoff_lat?.toFixed(4)}, ${ride.dropoff_lng?.toFixed(4)}`}
                            </p>
                        </div>
                    </div>
                </div>

                {/* Stats */}
                <div className="grid grid-cols-3 gap-2 text-center text-xs">
                    <div className="rounded-lg bg-muted p-2">
                        <p className="font-bold">{elapsed}m</p>
                        <p className="text-muted-foreground">Elapsed</p>
                    </div>
                    <div className="rounded-lg bg-muted p-2">
                        <p className="font-bold">
                            {ride.distance_km ? `${ride.distance_km.toFixed(1)} km` : "—"}
                        </p>
                        <p className="text-muted-foreground">Distance</p>
                    </div>
                    <div className="rounded-lg bg-muted p-2">
                        <p className="font-bold text-green-600">
                            {ride.total_fare ? `$${ride.total_fare.toFixed(2)}` : "—"}
                        </p>
                        <p className="text-muted-foreground">Fare</p>
                    </div>
                </div>

                {/* People */}
                <div className="space-y-2">
                    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        Rider
                    </p>
                    <div className="flex items-center gap-2 rounded-lg border border-border p-2">
                        <Avatar className="h-8 w-8">
                            <AvatarImage src={ride.rider_photo ?? undefined} />
                            <AvatarFallback className="text-xs">
                                {ride.rider_name.slice(0, 2).toUpperCase()}
                            </AvatarFallback>
                        </Avatar>
                        <div className="flex-1 min-w-0">
                            <p className="truncate text-xs font-medium">{ride.rider_name}</p>
                            {ride.rider_phone && (
                                <p className="text-xs text-muted-foreground">{ride.rider_phone}</p>
                            )}
                        </div>
                        {ride.rider_phone && (
                            <Button
                                size="icon"
                                variant="ghost"
                                className="h-6 w-6"
                                onClick={() => window.open(`tel:${ride.rider_phone}`)}
                            >
                                <Phone className="h-3 w-3" />
                            </Button>
                        )}
                    </div>
                </div>

                {ride.driver_id && (
                    <div className="space-y-2">
                        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                            Driver
                        </p>
                        <div
                            className="flex cursor-pointer items-center gap-2 rounded-lg border border-border p-2 hover:bg-muted"
                            onClick={() => ride.driver_id && onDriverClick(ride.driver_id)}
                        >
                            <Avatar className="h-8 w-8">
                                <AvatarFallback className="text-xs">
                                    {(ride.driver_name ?? "DR").slice(0, 2).toUpperCase()}
                                </AvatarFallback>
                            </Avatar>
                            <div className="flex-1 min-w-0">
                                <p className="truncate text-xs font-medium">{ride.driver_name ?? "—"}</p>
                                {ride.driver_phone && (
                                    <p className="text-xs text-muted-foreground">{ride.driver_phone}</p>
                                )}
                            </div>
                            {ride.driver_phone && (
                                <Button
                                    size="icon"
                                    variant="ghost"
                                    className="h-6 w-6"
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        window.open(`tel:${ride.driver_phone}`);
                                    }}
                                >
                                    <Phone className="h-3 w-3" />
                                </Button>
                            )}
                        </div>
                    </div>
                )}

                {/* Actions */}
                <Button
                    variant="destructive"
                    size="sm"
                    className="w-full gap-1.5 text-xs"
                    onClick={() => onCancelRide(ride.id)}
                >
                    <XCircle className="h-3.5 w-3.5" /> Cancel Ride
                </Button>
            </div>
        </div>
    );
}
```

- [ ] **Step 2: Commit**

```bash
git add src/app/dashboard/monitoring/ride-panel.tsx
git commit -m "feat(frontend): add ride detail panel component"
```

---

## Task 10: Frontend — Monitoring Map (Google Maps)

**Files:**
- Create: `src/app/dashboard/monitoring/monitoring-map.tsx`

- [ ] **Step 1: Create the imperative Google Maps component**

```typescript
// src/app/dashboard/monitoring/monitoring-map.tsx
"use client";

import { useCallback, useEffect, useRef } from "react";
import { GoogleMap, useJsApiLoader } from "@react-google-maps/api";
import { MonitoringDriver, MonitoringFilters, MonitoringRide, SelectedItem } from "./types";

const MAP_CONTAINER_STYLE = { width: "100%", height: "100%" };
const DEFAULT_CENTER = { lat: 43.6532, lng: -79.3832 }; // Toronto — update to your service area
const DEFAULT_ZOOM = 12;

// Colour tokens for driver markers
const DRIVER_COLOURS = {
    online_free: "#22c55e",   // green-500
    online_ride: "#f59e0b",   // amber-500
    offline: "#9ca3af",       // gray-400
};

function makeDriverIcon(driver: MonitoringDriver): google.maps.Symbol {
    let fill: string;
    if (!driver.is_online) fill = DRIVER_COLOURS.offline;
    else if (driver.active_ride_id) fill = DRIVER_COLOURS.online_ride;
    else fill = DRIVER_COLOURS.online_free;

    return {
        path: google.maps.SymbolPath.CIRCLE,
        fillColor: fill,
        fillOpacity: 1,
        strokeColor: "#ffffff",
        strokeWeight: 2,
        scale: 9,
    };
}

interface MonitoringMapProps {
    driversMap: React.MutableRefObject<Map<string, MonitoringDriver>>;
    ridesMap: React.MutableRefObject<Map<string, MonitoringRide>>;
    filters: MonitoringFilters;
    searchQuery: string;
    selected: SelectedItem;
    followMode: boolean;
    onSelectDriver: (id: string) => void;
    onSelectRide: (id: string) => void;
    /** Exposes imperative update handles to parent */
    onReady: (handles: MapHandles) => void;
}

export interface MapHandles {
    updateDriverMarker: (driver: MonitoringDriver) => void;
    removeDriverMarker: (driverId: string) => void;
    updateRideMarkers: (ride: MonitoringRide) => void;
    removeRideMarkers: (rideId: string) => void;
    panTo: (lat: number, lng: number) => void;
}

export function MonitoringMap({
    driversMap,
    ridesMap,
    filters,
    searchQuery,
    selected,
    followMode,
    onSelectDriver,
    onSelectRide,
    onReady,
}: MonitoringMapProps) {
    const apiKey = process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY ?? "";
    const { isLoaded, loadError } = useJsApiLoader({
        googleMapsApiKey: apiKey,
        id: "google-map-monitoring",
    });

    const mapRef = useRef<google.maps.Map | null>(null);
    const driverMarkersRef = useRef<Map<string, google.maps.Marker>>(new Map());
    // Rides: each ride has a pickup pin, dropoff pin, and optional route polyline
    const rideMarkersRef = useRef<
        Map<string, { pickup: google.maps.Marker; dropoff: google.maps.Marker; line: google.maps.Polyline | null }>
    >(new Map());

    const onSelectDriverRef = useRef(onSelectDriver);
    onSelectDriverRef.current = onSelectDriver;
    const onSelectRideRef = useRef(onSelectRide);
    onSelectRideRef.current = onSelectRide;

    // ── Imperative driver marker management ──────────────────────────
    const updateDriverMarker = useCallback((driver: MonitoringDriver) => {
        if (!mapRef.current || !driver.lat || !driver.lng) return;

        // Visibility: apply filter
        const visible =
            searchQuery
                ? driver.name.toLowerCase().includes(searchQuery.toLowerCase())
                : driver.is_online
                ? filters.showOnline
                : filters.showOffline;

        let marker = driverMarkersRef.current.get(driver.id);
        const position = { lat: driver.lat, lng: driver.lng };

        if (!marker) {
            marker = new google.maps.Marker({
                map: mapRef.current,
                position,
                icon: makeDriverIcon(driver),
                title: driver.name,
                visible,
                label: {
                    text: driver.name.slice(0, 1).toUpperCase(),
                    color: "#fff",
                    fontSize: "11px",
                    fontWeight: "bold",
                },
            });
            marker.addListener("click", () => onSelectDriverRef.current(driver.id));
            driverMarkersRef.current.set(driver.id, marker);
        } else {
            marker.setPosition(position);
            marker.setIcon(makeDriverIcon(driver));
            marker.setVisible(visible);
        }
    }, [filters, searchQuery]);

    const removeDriverMarker = useCallback((driverId: string) => {
        const m = driverMarkersRef.current.get(driverId);
        if (m) { m.setMap(null); driverMarkersRef.current.delete(driverId); }
    }, []);

    // ── Imperative ride marker management ────────────────────────────
    const updateRideMarkers = useCallback((ride: MonitoringRide) => {
        if (!mapRef.current) return;
        if (!filters.showRides) {
            const existing = rideMarkersRef.current.get(ride.id);
            if (existing) {
                existing.pickup.setVisible(false);
                existing.dropoff.setVisible(false);
                existing.line?.setVisible(false);
            }
            return;
        }

        let entry = rideMarkersRef.current.get(ride.id);
        if (!entry) {
            const pickup = new google.maps.Marker({
                map: mapRef.current,
                position: { lat: ride.pickup_lat, lng: ride.pickup_lng },
                icon: {
                    path: google.maps.SymbolPath.CIRCLE,
                    fillColor: "#3b82f6",
                    fillOpacity: 1,
                    strokeColor: "#fff",
                    strokeWeight: 2,
                    scale: 8,
                },
                label: { text: "P", color: "#fff", fontSize: "10px", fontWeight: "bold" },
                title: `Pickup: ${ride.pickup_address ?? ""}`,
            });
            pickup.addListener("click", () => onSelectRideRef.current(ride.id));

            const dropoff = new google.maps.Marker({
                map: mapRef.current,
                position: { lat: ride.dropoff_lat, lng: ride.dropoff_lng },
                icon: {
                    path: google.maps.SymbolPath.CIRCLE,
                    fillColor: "#ef4444",
                    fillOpacity: 1,
                    strokeColor: "#fff",
                    strokeWeight: 2,
                    scale: 8,
                },
                label: { text: "D", color: "#fff", fontSize: "10px", fontWeight: "bold" },
                title: `Dropoff: ${ride.dropoff_address ?? ""}`,
            });
            dropoff.addListener("click", () => onSelectRideRef.current(ride.id));

            const line = new google.maps.Polyline({
                map: mapRef.current,
                path: [
                    { lat: ride.pickup_lat, lng: ride.pickup_lng },
                    { lat: ride.dropoff_lat, lng: ride.dropoff_lng },
                ],
                strokeColor: "#3b82f6",
                strokeOpacity: 0.5,
                strokeWeight: 2,
                icons: [{ icon: { path: "M 0,-1 0,1", strokeOpacity: 1, scale: 3 }, offset: "0", repeat: "16px" }],
            });

            entry = { pickup, dropoff, line };
            rideMarkersRef.current.set(ride.id, entry);
        } else {
            entry.pickup.setPosition({ lat: ride.pickup_lat, lng: ride.pickup_lng });
            entry.dropoff.setPosition({ lat: ride.dropoff_lat, lng: ride.dropoff_lng });
            entry.pickup.setVisible(true);
            entry.dropoff.setVisible(true);
            entry.line?.setVisible(true);
        }
    }, [filters.showRides]);

    const removeRideMarkers = useCallback((rideId: string) => {
        const entry = rideMarkersRef.current.get(rideId);
        if (entry) {
            entry.pickup.setMap(null);
            entry.dropoff.setMap(null);
            entry.line?.setMap(null);
            rideMarkersRef.current.delete(rideId);
        }
    }, []);

    const panTo = useCallback((lat: number, lng: number) => {
        mapRef.current?.panTo({ lat, lng });
    }, []);

    // Register handles with parent once map is ready
    const onMapLoad = useCallback(
        (map: google.maps.Map) => {
            mapRef.current = map;

            // Seed initial markers from the ref maps passed by parent
            driversMap.current.forEach(updateDriverMarker);
            ridesMap.current.forEach(updateRideMarkers);

            onReady({ updateDriverMarker, removeDriverMarker, updateRideMarkers, removeRideMarkers, panTo });
        },
        [driversMap, ridesMap, updateDriverMarker, updateRideMarkers, removeDriverMarker, removeRideMarkers, panTo, onReady]
    );

    // Re-apply filter visibility when filters change
    useEffect(() => {
        driverMarkersRef.current.forEach((marker, id) => {
            const d = driversMap.current.get(id);
            if (!d) return;
            const vis = d.is_online ? filters.showOnline : filters.showOffline;
            marker.setVisible(vis);
        });
        rideMarkersRef.current.forEach(({ pickup, dropoff, line }) => {
            pickup.setVisible(filters.showRides);
            dropoff.setVisible(filters.showRides);
            line?.setVisible(filters.showRides);
        });
    }, [filters, driversMap]);

    // Follow selected driver
    useEffect(() => {
        if (!followMode || !selected || selected.type !== "driver") return;
        const d = driversMap.current.get(selected.id);
        if (d?.lat && d.lng) panTo(d.lat, d.lng);
    });

    if (loadError) {
        return (
            <div className="flex h-full items-center justify-center bg-muted">
                <p className="text-sm text-destructive">
                    Failed to load Google Maps. Check NEXT_PUBLIC_GOOGLE_MAPS_API_KEY.
                </p>
            </div>
        );
    }

    if (!isLoaded) {
        return (
            <div className="flex h-full items-center justify-center bg-muted">
                <p className="text-sm text-muted-foreground">Loading map…</p>
            </div>
        );
    }

    if (!apiKey) {
        return (
            <div className="flex h-full flex-col items-center justify-center gap-2 bg-muted">
                <p className="text-sm font-medium">Google Maps API key not set</p>
                <p className="text-xs text-muted-foreground">
                    Add NEXT_PUBLIC_GOOGLE_MAPS_API_KEY to admin-dashboard/.env.local and restart.
                </p>
            </div>
        );
    }

    return (
        <GoogleMap
            mapContainerStyle={MAP_CONTAINER_STYLE}
            center={DEFAULT_CENTER}
            zoom={DEFAULT_ZOOM}
            onLoad={onMapLoad}
            options={{
                disableDefaultUI: false,
                zoomControl: true,
                streetViewControl: false,
                mapTypeControl: false,
                fullscreenControl: true,
            }}
        />
    );
}
```

- [ ] **Step 2: Commit**

```bash
git add src/app/dashboard/monitoring/monitoring-map.tsx
git commit -m "feat(frontend): add imperative Google Maps monitoring component"
```

---

## Task 11: Frontend — Page Shell

**Files:**
- Create: `src/app/dashboard/monitoring/page.tsx`

- [ ] **Step 1: Create the page**

```typescript
// src/app/dashboard/monitoring/page.tsx
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { v4 as uuidv4 } from "uuid";
import { useAuthStore } from "@/store/authStore";
import { getMonitoringDrivers, getMonitoringRides, getServiceAreas, getVehicleTypes } from "@/lib/api";
import { useMonitoringSocket } from "@/hooks/use-monitoring-socket";
import { MonitoringMap, MapHandles } from "./monitoring-map";
import { MonitoringToolbar } from "./toolbar";
import { AlertFeed } from "./alert-feed";
import { DriverPanel } from "./driver-panel";
import { RidePanel } from "./ride-panel";
import {
    AlertEvent,
    MonitoringCounts,
    MonitoringDriver,
    MonitoringFilters,
    MonitoringRide,
    MonitoringWsEvent,
    SelectedItem,
} from "./types";

export default function MonitoringPage() {
    const token = useAuthStore((s) => s.token);

    // ── Ref-based data stores (never cause re-renders) ────────────────
    const driversRef = useRef<Map<string, MonitoringDriver>>(new Map());
    const ridesRef = useRef<Map<string, MonitoringRide>>(new Map());
    const mapHandlesRef = useRef<MapHandles | null>(null);

    // ── React state (causes re-renders) ──────────────────────────────
    const [counts, setCounts] = useState<MonitoringCounts>({ online: 0, onRide: 0, offline: 0, activeRides: 0 });
    const [filters, setFilters] = useState<MonitoringFilters>({
        showOnline: true,
        showOffline: false,
        showRides: true,
        serviceAreaId: null,
        vehicleTypeId: null,
    });
    const [searchQuery, setSearchQuery] = useState("");
    const [followMode, setFollowMode] = useState(false);
    const [selected, setSelected] = useState<SelectedItem>(null);
    const [alerts, setAlerts] = useState<AlertEvent[]>([]);
    const [serviceAreas, setServiceAreas] = useState<{ id: string; name: string }[]>([]);
    const [vehicleTypes, setVehicleTypes] = useState<{ id: string; name: string }[]>([]);

    // ── Derived selections ────────────────────────────────────────────
    const selectedDriver =
        selected?.type === "driver" ? driversRef.current.get(selected.id) ?? null : null;
    const selectedRide =
        selected?.type === "ride" ? ridesRef.current.get(selected.id) ?? null : null;

    // ── Count recalculation ───────────────────────────────────────────
    const recalcCounts = useCallback(() => {
        let online = 0, onRide = 0, offline = 0;
        driversRef.current.forEach((d) => {
            if (!d.is_online) offline++;
            else if (d.active_ride_id) onRide++;
            else online++;
        });
        setCounts({ online, onRide, offline, activeRides: ridesRef.current.size });
    }, []);

    // ── Push alert ────────────────────────────────────────────────────
    const pushAlert = useCallback((alert: Omit<AlertEvent, "id" | "timestamp">) => {
        setAlerts((prev) =>
            [{ ...alert, id: uuidv4(), timestamp: new Date().toISOString() }, ...prev].slice(0, 200)
        );
    }, []);

    // ── Initial data load ─────────────────────────────────────────────
    useEffect(() => {
        async function load() {
            const [drivers, rides, areas, vtypes] = await Promise.allSettled([
                getMonitoringDrivers(),
                getMonitoringRides(),
                getServiceAreas(),
                getVehicleTypes(),
            ]);

            if (drivers.status === "fulfilled") {
                drivers.value.forEach((d) => driversRef.current.set(d.id, d));
            }
            if (rides.status === "fulfilled") {
                rides.value.forEach((r) => ridesRef.current.set(r.id, r));
            }
            if (areas.status === "fulfilled") {
                setServiceAreas((areas.value as any[]).map((a) => ({ id: a.id, name: a.name })));
            }
            if (vtypes.status === "fulfilled") {
                setVehicleTypes((vtypes.value as any[]).map((v) => ({ id: v.id, name: v.name })));
            }
            recalcCounts();
        }
        load();
    }, [recalcCounts]);

    // ── WebSocket event handler ───────────────────────────────────────
    const handleWsEvent = useCallback(
        (event: MonitoringWsEvent) => {
            switch (event.type) {
                case "driver_location_update": {
                    const d = driversRef.current.get(event.driver_id);
                    if (d) {
                        const updated = { ...d, lat: event.lat, lng: event.lng };
                        driversRef.current.set(event.driver_id, updated);
                        mapHandlesRef.current?.updateDriverMarker(updated);
                        // Follow selected driver
                        if (followMode && selected?.type === "driver" && selected.id === event.driver_id) {
                            mapHandlesRef.current?.panTo(event.lat, event.lng);
                        }
                    }
                    break;
                }
                case "driver_status_changed": {
                    const d = driversRef.current.get(event.driver_id);
                    if (d) {
                        const updated = { ...d, is_online: event.is_online };
                        driversRef.current.set(event.driver_id, updated);
                        mapHandlesRef.current?.updateDriverMarker(updated);
                        recalcCounts();
                        pushAlert({
                            icon: event.is_online ? "online" : "offline",
                            message: `Driver ${d.name} went ${event.is_online ? "online" : "offline"}`,
                            driver_id: event.driver_id,
                        });
                    }
                    break;
                }
                case "ride_status_changed": {
                    const r = ridesRef.current.get(event.ride_id);
                    if (r) {
                        const updated = { ...r, status: event.status as MonitoringRide["status"] };
                        ridesRef.current.set(event.ride_id, updated);
                        mapHandlesRef.current?.updateRideMarkers(updated);
                        if (event.status === "completed" || event.status === "cancelled") {
                            ridesRef.current.delete(event.ride_id);
                            mapHandlesRef.current?.removeRideMarkers(event.ride_id);
                            if (selected?.type === "ride" && selected.id === event.ride_id) {
                                setSelected(null);
                            }
                            recalcCounts();
                            pushAlert({
                                icon: event.status === "completed" ? "ride_done" : "ride_cancelled",
                                message: `Ride #${event.ride_id.slice(-8)} ${event.status}${r.total_fare ? ` — $${r.total_fare.toFixed(2)}` : ""}`,
                                ride_id: event.ride_id,
                            });
                        }
                    }
                    break;
                }
                case "ride_requested": {
                    ridesRef.current.set(event.ride.id, event.ride);
                    mapHandlesRef.current?.updateRideMarkers(event.ride);
                    recalcCounts();
                    pushAlert({
                        icon: "ride_new",
                        message: `New ride #${event.ride.id.slice(-8)} requested`,
                        ride_id: event.ride.id,
                    });
                    break;
                }
            }
        },
        [followMode, selected, recalcCounts, pushAlert]
    );

    const { status: wsStatus } = useMonitoringSocket({ token, onEvent: handleWsEvent });

    // ── Selection helpers ─────────────────────────────────────────────
    const selectDriver = useCallback(
        (id: string) => {
            setSelected({ type: "driver", id });
            const d = driversRef.current.get(id);
            if (d?.lat && d.lng) mapHandlesRef.current?.panTo(d.lat, d.lng);
        },
        []
    );

    const selectRide = useCallback(
        (id: string) => {
            setSelected({ type: "ride", id });
            const r = ridesRef.current.get(id);
            if (r) mapHandlesRef.current?.panTo(r.pickup_lat, r.pickup_lng);
        },
        []
    );

    const handleAlertClick = useCallback(
        (evt: AlertEvent) => {
            if (evt.driver_id) selectDriver(evt.driver_id);
            else if (evt.ride_id) selectRide(evt.ride_id);
        },
        [selectDriver, selectRide]
    );

    return (
        <div className="flex h-screen flex-col overflow-hidden">
            <MonitoringToolbar
                counts={counts}
                filters={filters}
                onFilterChange={(f) => setFilters((prev) => ({ ...prev, ...f }))}
                searchQuery={searchQuery}
                onSearchChange={setSearchQuery}
                followMode={followMode}
                onFollowToggle={() => setFollowMode((v) => !v)}
                serviceAreas={serviceAreas}
                vehicleTypes={vehicleTypes}
                wsStatus={wsStatus}
            />

            <div className="flex flex-1 overflow-hidden">
                {/* Map */}
                <div className="relative flex-1">
                    <MonitoringMap
                        driversMap={driversRef}
                        ridesMap={ridesRef}
                        filters={filters}
                        searchQuery={searchQuery}
                        selected={selected}
                        followMode={followMode}
                        onSelectDriver={selectDriver}
                        onSelectRide={selectRide}
                        onReady={(handles) => { mapHandlesRef.current = handles; }}
                    />
                </div>

                {/* Detail panel */}
                <div className="w-80 shrink-0 overflow-hidden border-l border-border">
                    {selectedDriver ? (
                        <DriverPanel
                            driver={selectedDriver}
                            onRideClick={selectRide}
                        />
                    ) : selectedRide ? (
                        <RidePanel
                            ride={selectedRide}
                            onDriverClick={selectDriver}
                            onCancelRide={(id) => {
                                // TODO: wire to cancel ride API endpoint
                                console.log("Cancel ride", id);
                            }}
                        />
                    ) : (
                        <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
                            <div className="rounded-full bg-muted p-4">
                                <span className="text-2xl">🗺️</span>
                            </div>
                            <p className="font-medium">Select a driver or ride</p>
                            <p className="text-xs text-muted-foreground">
                                Click any marker on the map to see details here
                            </p>
                        </div>
                    )}
                </div>
            </div>

            <AlertFeed
                events={alerts}
                onClear={() => setAlerts([])}
                onEventClick={handleAlertClick}
            />
        </div>
    );
}
```

- [ ] **Step 2: Install uuid (if not already installed)**

```bash
cd admin-dashboard
npm install uuid && npm install --save-dev @types/uuid
```

- [ ] **Step 3: Commit**

```bash
git add src/app/dashboard/monitoring/page.tsx
git commit -m "feat(frontend): add live monitoring page shell with full state orchestration"
```

---

## Task 12: Frontend — Sidebar Entry

**Files:**
- Modify: `src/components/sidebar.tsx`

- [ ] **Step 1: Add Radar icon import**

In `src/components/sidebar.tsx`, add `Radar` to the lucide-react import line:

```typescript
import {
    LayoutDashboard, Car, Users, DollarSign, Settings, MapPin, Ticket,
    HelpCircle, Flame, Building2, LifeBuoy,
    LogOut, Menu, FileText, X, CreditCard, ChevronLeft, ChevronRight,
    Sun, Moon, Shield, Cloud, Radar,
} from "lucide-react";
```

- [ ] **Step 2: Add "Live Monitor" to the Operations nav group**

In `sidebar.tsx`, find the Operations group (around line 38) and add the monitoring entry:

```typescript
    {
        title: "Operations",
        items: [
            { href: "/dashboard/rides", label: "Rides", icon: Car, module: "rides" },
            { href: "/dashboard/drivers", label: "Drivers", icon: Car, module: "drivers" },
            { href: "/dashboard/users", label: "Users", icon: Users, module: "users" },
            { href: "/dashboard/heatmap", label: "Heat Map", icon: Flame, module: "heatmap" },
            { href: "/dashboard/monitoring", label: "Live Monitor", icon: Radar, module: "dashboard" },
        ],
    },
```

- [ ] **Step 3: Verify page loads in browser**

Navigate to `http://localhost:3000/dashboard/monitoring`. Expected:
- "Live Monitor" appears in sidebar under Operations
- Toolbar renders with counters (may all be 0 in dev)
- Map renders (placeholder if no API key, or Google Maps tiles if key set)
- WS status shows "connecting" then "connected" or "disconnected"
- Detail panel shows empty state

- [ ] **Step 4: Commit**

```bash
git add src/components/sidebar.tsx
git commit -m "feat(frontend): add Live Monitor to sidebar nav"
```

---

## Post-Implementation: Add Google Maps API Key

Once you have a key from Google Cloud Console (Maps JavaScript API enabled):

```
# admin-dashboard/.env.local
NEXT_PUBLIC_GOOGLE_MAPS_API_KEY=AIzaSy...your-key...
```

Restart Next.js dev server. The map will load with full tiles and markers.

Also update `DEFAULT_CENTER` in `monitoring-map.tsx` to your service area's coordinates.
