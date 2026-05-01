"""
Reproduce the driver "cannot go online" issue without the mobile app.

Simulates a minimal driver client: logs in via OTP, opens the WebSocket,
authenticates, calls PUT /drivers/{id}/status to go online, then keeps the
socket alive with pong replies while polling GET /drivers/me every 5s.

The goal is to isolate whether the driver gets kicked offline by the
server (sweeper, WS race, disconnect handler) versus by the driver app's
own lifecycle churn — if THIS script stays online cleanly, the bug is
client-side; if THIS script also gets flipped offline, the bug is server-side.

Usage:
    cd spinr/backend
    python diagnose_driver_ws.py \
        --base-url https://your-backend.up.railway.app \
        --phone +13065203307 \
        --code 1234 \
        --duration 90

Run from a terminal with network access to the deployed backend.
Prints every event with a wall-clock timestamp so the trace lines up
with the backend logs.
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone

import httpx
import websockets


def ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def log(tag: str, msg: str) -> None:
    print(f"[{ts()}] {tag:<12} {msg}", flush=True)


async def login(base_url: str, phone: str, code: str) -> dict:
    """Returns {'token', 'user_id', 'role', 'driver_id'?}."""
    api = f"{base_url.rstrip('/')}/api/v1"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{api}/auth/send-otp", json={"phone": phone})
        log("LOGIN", f"send-otp {r.status_code} {r.text[:160]}")

        r = await client.post(
            f"{api}/auth/verify-otp", json={"phone": phone, "code": code}
        )
        if r.status_code != 200:
            log("LOGIN", f"verify-otp FAILED {r.status_code} {r.text}")
            sys.exit(1)
        data = r.json()
        token = data.get("token") or data.get("access_token")
        user = data.get("user") or {}
        user_id = user.get("id")
        role = user.get("role")
        log("LOGIN", f"ok user_id={user_id} role={role}")

        # /auth/me (re-derives driver_onboarding_status server-side)
        r = await client.get(
            f"{api}/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        me = r.json() if r.status_code == 200 else {}
        log(
            "AUTH/ME",
            f"role={me.get('role')} is_driver={me.get('is_driver')} "
            f"profile_complete={me.get('profile_complete')} "
            f"driver_onboarding_status={me.get('driver_onboarding_status')!r} "
            f"detail={me.get('driver_onboarding_detail')!r}",
        )

        # Full driver row dump — shows whether _has_vehicle would pass on backend.
        driver_id = None
        r = await client.get(
            f"{api}/drivers/me", headers={"Authorization": f"Bearer {token}"}
        )
        if r.status_code == 200:
            d = r.json() or {}
            driver_id = d.get("id")
            log("DRIVERS/ME", f"id={driver_id} user_id={d.get('user_id')}")
            log(
                "DRIVERS/ME",
                f"vehicle_make={d.get('vehicle_make')!r} "
                f"vehicle_model={d.get('vehicle_model')!r} "
                f"license_plate={d.get('license_plate')!r} "
                f"vehicle_type_id={d.get('vehicle_type_id')!r}",
            )
            log(
                "DRIVERS/ME",
                f"is_verified={d.get('is_verified')} "
                f"status={d.get('status')} "
                f"service_area_id={d.get('service_area_id')!r} "
                f"city={d.get('city')!r}",
            )
            # Mirror backend _has_vehicle() logic
            has_vehicle = bool(
                d.get("vehicle_make")
                and d.get("vehicle_model")
                and d.get("license_plate")
                and d.get("vehicle_type_id")
            )
            log("CHECK", f"_has_vehicle = {has_vehicle}")
        else:
            log("DRIVERS/ME", f"{r.status_code} {r.text[:200]}")

        return {"token": token, "user_id": user_id, "role": role, "driver_id": driver_id}


async def poll_driver_me(
    base_url: str, token: str, stop_event: asyncio.Event, interval: int
) -> None:
    api = f"{base_url.rstrip('/')}/api/v1"
    async with httpx.AsyncClient(timeout=20) as client:
        while not stop_event.is_set():
            try:
                r = await client.get(
                    f"{api}/drivers/me", headers={"Authorization": f"Bearer {token}"}
                )
                row = r.json() if r.status_code == 200 else {}
                log(
                    "POLL",
                    f"is_online={row.get('is_online')} "
                    f"is_available={row.get('is_available')} "
                    f"status={row.get('status')} "
                    f"last_status_changed_at={row.get('last_status_changed_at')}",
                )
            except Exception as e:
                log("POLL", f"error {e}")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass


async def go_online(base_url: str, token: str, driver_id: str) -> None:
    api = f"{base_url.rstrip('/')}/api/v1"
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.put(
            f"{api}/drivers/{driver_id}/status",
            headers={"Authorization": f"Bearer {token}"},
            json={"is_online": True},
        )
        log("GO-ONLINE", f"PUT /drivers/{driver_id}/status -> {r.status_code} {r.text[:200]}")


async def run_ws_session(
    base_url: str, token: str, user_id: str, duration: int, driver_id: str | None
) -> None:
    ws_url = (
        base_url.replace("https://", "wss://").replace("http://", "ws://").rstrip("/")
        + f"/ws/driver/{user_id}"
    )
    log("WS", f"connecting {ws_url}")

    start = time.monotonic()
    try:
        async with websockets.connect(ws_url, ping_interval=None, close_timeout=5) as ws:
            elapsed = time.monotonic() - start
            log("WS", f"connected in {elapsed*1000:.0f}ms; sending auth")
            await ws.send(json.dumps({"type": "auth", "token": token}))

            stop_event = asyncio.Event()
            poll_task = asyncio.create_task(
                poll_driver_me(base_url, token, stop_event, interval=5)
            )

            went_online = False
            deadline = time.monotonic() + duration
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 1.0))
                except asyncio.TimeoutError:
                    pass
                else:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        msg = {"raw": raw[:200]}
                    mtype = msg.get("type")
                    log("WS<-", json.dumps(msg)[:300])
                    if mtype == "ping":
                        await ws.send(json.dumps({"type": "pong"}))
                        log("WS->", "pong")
                    elif mtype == "auth_success" and not went_online and driver_id:
                        # Fire GO-ONLINE concurrently so we don't block the recv loop
                        asyncio.create_task(go_online(base_url, token, driver_id))
                        went_online = True

            stop_event.set()
            await poll_task
            log("WS", f"session window elapsed ({duration}s); closing cleanly")
            await ws.close(code=1000, reason="diagnostic_done")
    except websockets.ConnectionClosed as e:
        log("WS", f"ConnectionClosed code={e.code} reason={e.reason!r} rcvd={e.rcvd} sent={e.sent}")
    except Exception as e:
        log("WS", f"error {type(e).__name__}: {e}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="e.g. https://api.example.up.railway.app")
    parser.add_argument("--phone", required=True, help="E.164, e.g. +13065203307")
    parser.add_argument("--code", default="1234", help="OTP code (dev bypass=1234)")
    parser.add_argument("--duration", type=int, default=60, help="Seconds to hold the WS open")
    args = parser.parse_args()

    auth = await login(args.base_url, args.phone, args.code)
    if not auth["token"] or not auth["user_id"]:
        log("FATAL", "could not get token/user_id")
        sys.exit(1)
    if auth["role"] != "driver":
        log("WARN", f"user role is {auth['role']!r}, proceeding anyway")

    await run_ws_session(
        args.base_url,
        auth["token"],
        auth["user_id"],
        args.duration,
        auth["driver_id"],
    )


if __name__ == "__main__":
    asyncio.run(main())
