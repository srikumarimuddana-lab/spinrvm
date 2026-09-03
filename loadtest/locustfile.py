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

T16 ROUND 2 HARNESS FIX — pre-authenticated token cache instead of
per-spawn OTP login:
Round 1 found that `on_start` calling send-otp+verify-otp for every bot at
ramp-up drove the whole 60-bot pool (sharing one egress IP) straight
through the 6/min send-otp and 5/min verify-otp per-IP rate limits (a
harness capacity artifact, not a platform finding — see
docs/audit/2026-09-02-t16-staging-load-test-results.md). Fix: run
`preauth_bots.py` ONCE before the timed test, staggered at 4 logins/min
(below the 5/min verify-otp ceiling), which authenticates every bot user
up front and writes tokens to a JSON cache (`results/bot_tokens.json` by
default, `LOADTEST_TOKEN_CACHE` to override). `on_start` below reads a
cached token instead of touching the OTP endpoints at all — so the timed
run itself never exercises the OTP rate limiter, and the real limiter in
backend/routes/auth.py is completely untouched. Falls back to a live OTP
login (single bot, no stagger) only when the cache is missing/exhausted —
fine for a quick ad-hoc smoke test with a handful of users, but NOT a
substitute for pre-auth at the full 60-bot scale (it would just reproduce
the round-1 failure mode).
"""

from __future__ import annotations

import json
import os
import random
import threading
import time

import gevent
from locust import HttpUser, between, events, task

try:
    import websocket  # websocket-client — used by DriverBot for offers/pings
except ImportError:  # harness still runs rider-only without it
    websocket = None

from bot_common import API, BASE_OTP, TOKEN_CACHE_PATH, jitter_point, next_phone

_jitter_point = jitter_point  # kept as a module-local alias; call sites unchanged below
_next_phone = next_phone

# ride_id -> pickup OTP, published by the rider bot that booked the ride so
# the driver bot that wins the offer can verify pickup. In-process is correct:
# this harness owns both sides of the marketplace.
RIDE_OTPS: dict[str, str] = {}

# ── Pre-authenticated token cache (T16 round 2 harness fix) ────────────────
# Loaded once per Locust worker process; each on_start pops the next unused
# entry for its bot kind. A lock guards the pop because Locust spawns bots
# across gevent greenlets that could interleave between the list-index read
# and the append to `_used`, even though gevent itself is cooperative —
# cheap insurance, not a measured race.
_token_cache_lock = threading.Lock()
_token_cache: dict[str, list[dict]] | None = None
_token_cache_idx = {"rider": 0, "driver": 0}


def _load_token_cache() -> dict[str, list[dict]] | None:
    global _token_cache
    if _token_cache is not None:
        return _token_cache
    if not os.path.exists(TOKEN_CACHE_PATH):
        return None
    with open(TOKEN_CACHE_PATH, "r", encoding="utf-8") as f:
        _token_cache = json.load(f)
    return _token_cache


def _next_cached_credential(kind: str) -> dict | None:
    """Pop the next unused pre-authenticated bot for `kind`, if the cache has one."""
    cache = _load_token_cache()
    if not cache:
        return None
    pool = cache.get(f"{kind}s") or []
    with _token_cache_lock:
        idx = _token_cache_idx[kind]
        if idx >= len(pool):
            return None
        _token_cache_idx[kind] = idx + 1
        return pool[idx]


def _live_login(client, phone: str) -> tuple[str, str | None, dict]:
    """Fall-back path: log a single bot in live via OTP (no stagger).

    Only safe for small ad-hoc runs — at the full 60-bot pool this
    reproduces the exact rate-limit artifact preauth_bots.py exists to
    avoid. See module docstring.
    """
    client.post(f"{API}/auth/send-otp", json={"phone": phone}, name="auth:send-otp")
    r = client.post(
        f"{API}/auth/verify-otp",
        # T16 round-1 fix: VerifyOTPRequest's field is "code", not "otp",
        # and consent_accepted=True is required for a first-time signup
        # (PIPEDA explicit-consent gate, routes/auth.py ~:1177).
        json={"phone": phone, "code": BASE_OTP, "consent_accepted": True},
        name="auth:verify-otp",
    )
    r.raise_for_status()
    body = r.json()
    # AuthResponse (schemas.py) names the access token "token", not
    # "access_token" — same never-executed-harness mismatch as round 1.
    return body["token"], body.get("refresh_token"), body.get("user") or {}


def _login(client, kind: str) -> tuple[str, str | None, dict]:
    """Get a bot credential: prefer the pre-auth cache, fall back to live OTP.

    Returns (access_token, refresh_token, user_dict).
    """
    cached = _next_cached_credential(kind)
    if cached is not None:
        return cached["token"], cached.get("refresh_token"), cached.get("user") or {}
    # Cache missing/exhausted — live login, single bot, no stagger. Fine for
    # a small smoke test; at 60 bots this recreates the round-1 429 storm.
    phone = _next_phone(kind)
    return _live_login(client, phone)


def _refresh_access_token(client, refresh_token: str) -> str | None:
    """Exchange a refresh token for a fresh access token (POST /auth/refresh).

    /auth/refresh is limited to 20/minute per IP (backend/routes/auth.py:1675)
    — a much looser ceiling than the OTP endpoints, and refreshes are spread
    across the run's duration rather than bunched at spawn, so 60 bots each
    refreshing once near their 15-minute access-token expiry (rider/driver
    TTL per AGENTS.md) stays comfortably under it. Returns None on failure
    (caller keeps using the old token; the next request's 401 will be the
    real signal something is wrong).
    """
    if not refresh_token:
        return None
    try:
        r = client.post(
            f"{API}/auth/refresh",
            json={"refresh_token": refresh_token},
            name="auth:refresh",
        )
        if r.status_code != 200:
            return None
        return r.json().get("token")
    except Exception:
        return None


class RiderBot(HttpUser):
    weight = 3
    wait_time = between(5, 20)

    def on_start(self):
        token, refresh_token, user = _login(self.client, "rider")
        self.client.headers["Authorization"] = f"Bearer {token}"
        self.refresh_token = refresh_token
        self.user_id = user.get("id")
        # T16 round-2 fix: tokens now come from the pre-auth cache and are
        # reused for the whole run instead of re-logging-in per spawn — see
        # module docstring. Access tokens are short-lived (15 min per
        # AGENTS.md); refresh proactively partway through so a run longer
        # than that window (e.g. Scenario B's 30 min) doesn't start 401ing
        # near the end. /auth/refresh is a much looser 20/minute-per-IP
        # limit than the OTP endpoints and refreshes are spread out by each
        # bot's own random jitter below, not bunched at spawn.
        if self.refresh_token:
            gevent.spawn_later(random.uniform(600, 720), self._proactive_refresh)

    def _proactive_refresh(self):
        new_token = _refresh_access_token(self.client, self.refresh_token)
        if new_token:
            self.client.headers["Authorization"] = f"Bearer {new_token}"

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
                # T16 staging-run fix: /rides/estimate nests the vehicle type
                # under choice["vehicle_type"]["id"] (estimates.py:613/629),
                # not a top-level "vehicle_type_id"/"id" field — the previous
                # lookup always evaluated to None, which is why every
                # rides:create 422'd with "vehicle_type_id: Input should be
                # a valid string" the first time this harness was actually
                # run. See docs/audit/2026-09-02-t16-staging-load-test-results.md.
                "vehicle_type_id": (choice.get("vehicle_type") or {}).get("id"),
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
        token, refresh_token, user = _login(self.client, "driver")
        self.token = token
        self.refresh_token = refresh_token
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
        # T16 round-2 fix: see RiderBot.on_start — same reused-token +
        # proactive-refresh pattern. self.token (used by _ws_loop's auth
        # frame) is updated too, so a reconnect after refresh still
        # authenticates with a live token.
        if self.refresh_token:
            gevent.spawn_later(random.uniform(600, 720), self._proactive_refresh)

    def _proactive_refresh(self):
        new_token = _refresh_access_token(self.client, self.refresh_token)
        if new_token:
            self.token = new_token
            self.client.headers["Authorization"] = f"Bearer {new_token}"

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
