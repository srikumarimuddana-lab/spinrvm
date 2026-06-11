"""Spinr marketplace load simulation (ACTION_ITEMS E2).

Simulates the two-sided marketplace against a STAGING environment:

  RiderBot   — estimate → book → poll until completed/cancelled
  DriverBot  — go online → WebSocket auth → 1 Hz GPS pings (exercises the
               B3 location hot path) → accept offers → arrive → verify OTP
               → complete

Matchmaking is real: dispatch offers rides to whichever bot drivers are
nearest/available, the offer arrives on the driver's WebSocket as
`new_ride_assignment`, and the accept race (409 ride_taken) is exercised
whenever multiple bots hold offers. Rider and driver bots share the pickup
OTP through an in-process registry keyed by ride_id (both sides of the
marketplace live in this Locust process).

NEVER point this at production: it books real rides. The dev OTP bypass
("1234") only works when the target's ENV != production, which doubles as
a safety interlock.

Run (single process is fine up to ~200 bots; use --processes beyond):

  pip install -r requirements.txt
  export LOADTEST_BASE_URL=https://staging-api.spinr.ca
  locust -f locustfile.py --headless -u 60 -r 2 -t 10m \
      --host "$LOADTEST_BASE_URL" --csv results/run1

Env knobs:
  LOADTEST_OTP            login OTP (default 1234 — staging dev bypass)
  LOADTEST_PHONE_PREFIX   bot phone prefix (default +1306555)  riders get
                          even suffixes, drivers odd — seed staging
                          accordingly (see README "Seeding").
  LOADTEST_CENTER_LAT/LNG service-area centre (default Saskatoon downtown)

SLA gates (CLAUDE.md performance table) are asserted at test end via the
test_stop hook: fare estimate P95 < 300 ms, accept round-trip P95 < 2 s.
"""

from __future__ import annotations

import json
import os
import random
import time

import gevent
from locust import HttpUser, between, events, task

try:
    import websocket  # websocket-client — used by DriverBot for offers/pings
except ImportError:  # harness still runs rider-only without it
    websocket = None

BASE_OTP = os.environ.get("LOADTEST_OTP", "1234")
PHONE_PREFIX = os.environ.get("LOADTEST_PHONE_PREFIX", "+1306555")
CENTER_LAT = float(os.environ.get("LOADTEST_CENTER_LAT", "52.1332"))
CENTER_LNG = float(os.environ.get("LOADTEST_CENTER_LNG", "-106.6700"))
API = "/api/v1"

# ride_id -> pickup OTP, published by the rider bot that booked the ride so
# the driver bot that wins the offer can verify pickup. In-process is correct:
# this harness owns both sides of the marketplace.
RIDE_OTPS: dict[str, str] = {}

_phone_counter = {"rider": 0, "driver": 0}


def _next_phone(kind: str) -> str:
    # Riders take even suffixes, drivers odd, so the two pools never collide.
    _phone_counter[kind] += 1
    suffix = _phone_counter[kind] * 2 + (1 if kind == "driver" else 0)
    return f"{PHONE_PREFIX}{suffix:04d}"


def _jitter_point(radius_deg: float = 0.02) -> tuple[float, float]:
    return (
        CENTER_LAT + random.uniform(-radius_deg, radius_deg),
        CENTER_LNG + random.uniform(-radius_deg, radius_deg),
    )


def _login(client, phone: str) -> tuple[str, dict]:
    client.post(f"{API}/auth/send-otp", json={"phone": phone}, name="auth:send-otp")
    r = client.post(
        f"{API}/auth/verify-otp",
        json={"phone": phone, "otp": BASE_OTP},
        name="auth:verify-otp",
    )
    r.raise_for_status()
    body = r.json()
    return body["access_token"], body.get("user") or {}


class RiderBot(HttpUser):
    weight = 3
    wait_time = between(5, 20)

    def on_start(self):
        self.phone = _next_phone("rider")
        token, user = _login(self.client, self.phone)
        self.client.headers["Authorization"] = f"Bearer {token}"
        self.user_id = user.get("id")

    @task
    def ride_lifecycle(self):
        pickup = _jitter_point()
        dropoff = _jitter_point()

        # 1. Estimate — the <300ms P95 SLA path.
        est = self.client.post(
            f"{API}/rides/estimate",
            json={
                "pickup_lat": pickup[0],
                "pickup_lng": pickup[1],
                "dropoff_lat": dropoff[0],
                "dropoff_lng": dropoff[1],
            },
            name="rides:estimate",
        )
        if est.status_code != 200:
            return
        options = est.json().get("estimates") or est.json().get("fares") or []
        if not options:
            return
        choice = options[0]

        # 2. Book.
        book = self.client.post(
            f"{API}/rides",
            json={
                "vehicle_type_id": choice.get("vehicle_type_id") or choice.get("id"),
                "pickup_address": "Loadtest pickup",
                "pickup_lat": pickup[0],
                "pickup_lng": pickup[1],
                "dropoff_address": "Loadtest dropoff",
                "dropoff_lat": dropoff[0],
                "dropoff_lng": dropoff[1],
                "payment_method": "cash",
                "estimate_token": choice.get("estimate_token"),
            },
            name="rides:create",
        )
        if book.status_code not in (200, 201):
            return
        ride = book.json()
        ride_id = ride.get("id") or (ride.get("ride") or {}).get("id")
        if not ride_id:
            return
        otp = ride.get("pickup_otp") or (ride.get("ride") or {}).get("pickup_otp")
        if otp:
            RIDE_OTPS[ride_id] = str(otp)

        # 3. Poll to terminal state — measures end-to-end match + trip time.
        requested_at = time.monotonic()
        accepted_at = None
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            gevent.sleep(3)
            r = self.client.get(f"{API}/rides/{ride_id}", name="rides:poll")
            if r.status_code != 200:
                continue
            status = r.json().get("status")
            if status in ("driver_accepted", "driver_arrived", "in_progress") and accepted_at is None:
                accepted_at = time.monotonic()
                events.request.fire(
                    request_type="MARKET",
                    name="market:request-to-accept",
                    response_time=(accepted_at - requested_at) * 1000,
                    response_length=0,
                    exception=None,
                )
            if status in ("completed", "cancelled"):
                RIDE_OTPS.pop(ride_id, None)
                return
        # No terminal state inside the window: cancel so the bot driver frees up.
        self.client.post(f"{API}/rides/{ride_id}/cancel", json={"reason": "loadtest timeout"}, name="rides:cancel")
        RIDE_OTPS.pop(ride_id, None)


class DriverBot(HttpUser):
    weight = 1
    wait_time = between(1, 2)

    def on_start(self):
        self.phone = _next_phone("driver")
        token, user = _login(self.client, self.phone)
        self.token = token
        self.client.headers["Authorization"] = f"Bearer {token}"
        self.user_id = user.get("id")
        self.lat, self.lng = _jitter_point()
        self.active_ride_id: str | None = None
        self.ws = None

        me = self.client.get(f"{API}/drivers/me", name="drivers:me")
        me.raise_for_status()
        self.driver_id = (me.json().get("driver") or me.json()).get("id")
        self.client.put(
            f"{API}/drivers/{self.driver_id}/status",
            json={"is_online": True, "lat": self.lat, "lng": self.lng},
            name="drivers:go-online",
        )
        if websocket is not None:
            gevent.spawn(self._ws_loop)

    def on_stop(self):
        try:
            self.client.put(
                f"{API}/drivers/{self.driver_id}/status",
                json={"is_online": False},
                name="drivers:go-offline",
            )
            if self.ws:
                self.ws.close()
        except Exception:
            pass

    # ── WebSocket: offers in, nothing else (pings go out from the task) ──
    def _ws_loop(self):
        ws_url = self.host.replace("https://", "wss://").replace("http://", "ws://")
        while True:
            try:
                self.ws = websocket.create_connection(f"{ws_url}/ws/driver/{self.user_id}", timeout=10)
                self.ws.send(json.dumps({"type": "auth", "token": self.token}))
                while True:
                    raw = self.ws.recv()
                    msg = json.loads(raw) if raw else {}
                    if msg.get("type") == "ping":
                        self.ws.send(json.dumps({"type": "pong"}))
                    elif msg.get("type") == "new_ride_assignment" and not self.active_ride_id:
                        self._handle_offer(msg["ride_id"], time.monotonic())
            except Exception:
                gevent.sleep(2)  # reconnect backoff; bot may also be stopping

    def _handle_offer(self, ride_id: str, offered_at: float):
        r = self.client.post(f"{API}/drivers/rides/{ride_id}/accept", name="drivers:accept")
        if r.status_code == 409:
            return  # lost the race — that's the guard working
        if r.status_code != 200:
            return
        events.request.fire(
            request_type="MARKET",
            name="market:offer-to-accept",
            response_time=(time.monotonic() - offered_at) * 1000,
            response_length=0,
            exception=None,
        )
        self.active_ride_id = ride_id
        gevent.spawn(self._drive_ride, ride_id)

    def _drive_ride(self, ride_id: str):
        try:
            gevent.sleep(random.uniform(4, 10))  # "drive" to pickup
            self.client.post(f"{API}/drivers/rides/{ride_id}/arrive", name="drivers:arrive")
            # Wait for the rider bot to publish the pickup OTP.
            otp = None
            for _ in range(20):
                otp = RIDE_OTPS.get(ride_id)
                if otp:
                    break
                gevent.sleep(1)
            if otp:
                self.client.post(
                    f"{API}/drivers/rides/{ride_id}/verify-otp",
                    json={"otp": otp},
                    name="drivers:verify-otp",
                )
            else:
                self.client.post(f"{API}/drivers/rides/{ride_id}/start", name="drivers:start")
            gevent.sleep(random.uniform(8, 20))  # the trip
            self.client.post(f"{API}/drivers/rides/{ride_id}/complete", name="drivers:complete")
        finally:
            self.active_ride_id = None

    @task
    def gps_ping(self):
        """1-2 s location cadence — the B3 hot path (<150ms write SLA)."""
        self.lat += random.uniform(-0.0005, 0.0005)
        self.lng += random.uniform(-0.0005, 0.0005)
        if self.ws is not None:
            try:
                self.ws.send(
                    json.dumps(
                        {
                            "type": "driver_location",
                            "lat": self.lat,
                            "lng": self.lng,
                            "heading": random.uniform(0, 360),
                            "speed": random.uniform(0, 18),
                        }
                    )
                )
                return
            except Exception:
                pass
        # WS unavailable → REST batch fallback keeps load on the write path.
        self.client.post(
            f"{API}/drivers/location-batch",
            json={"points": [{"lat": self.lat, "lng": self.lng}]},
            name="drivers:location-batch",
        )


# ── SLA gates (CLAUDE.md performance table) ─────────────────────────────


@events.test_stop.add_listener
def assert_slas(environment, **kwargs):
    stats = environment.stats
    # The request method must be looked up explicitly: stats.get() CREATES an
    # empty (truthy) entry for a missing (name, method) pair, so a
    # `get(name, "POST") or get(name, "MARKET")` chain never reaches the
    # MARKET entry and the dispatch gate would be silently skipped.
    gates = [
        ("rides:estimate", "POST", 0.95, 300, "fare estimate P95 < 300ms"),
        ("market:offer-to-accept", "MARKET", 0.95, 2000, "dispatch offer→accept P95 < 2s"),
    ]
    failed = []
    for name, method, pct, limit_ms, label in gates:
        entry = stats.get(name, method)
        if entry is None or entry.num_requests == 0:
            continue
        observed = entry.get_response_time_percentile(pct)
        if observed > limit_ms:
            failed.append(f"{label}: observed P{int(pct * 100)}={observed:.0f}ms")
    if failed:
        environment.process_exit_code = 1
        print("\nSLA GATES FAILED:\n  " + "\n  ".join(failed))
    else:
        print("\nAll SLA gates passed.")
