"""Standalone external controller; observe-only unless explicitly activated."""

import logging
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from api import Fly
from controller import Controller, inventory
from policy import Policy
from telemetry import collect, readiness

logger = logging.getLogger(__name__)
ACTION_REQUIRED = {
    "invalid_inventory",
    "machine_transition",
    "missing_metrics",
    "invalid_metrics",
    "dependency_unready",
    "attempt_budget",
    "capacity_exhausted",
    "start_failed",
}


def dry_run_setting(value):
    if value not in ("true", "false"):
        raise ValueError("BURST_DRY_RUN must be true or false")
    return value == "true"


class Runtime:
    def __init__(self, fly, anchor, app, org, token, dry_run, clock=time.time):
        self.fly, self.app, self.org, self.token = fly, app, org, token
        self.clock, self.dry_run = clock, dry_run
        self.controller = Controller(fly, anchor, clock)
        self.metrics = {
            "spinr_burst_running_machines": 0,
            "spinr_burst_last_observation_timestamp_seconds": 0,
            "spinr_burst_action_required": 1,
            "spinr_burst_last_failed_start_timestamp_seconds": 0,
            "spinr_burst_dry_run": int(dry_run),
        }
        self.lock = threading.Lock()

    def cycle(self):
        try:
            raw = self.fly.machines()
            machines = inventory(raw)
            with self.lock:
                self.metrics["spinr_burst_running_machines"] = sum(
                    m.state == "started" for m in machines
                )
            samples = collect(self.app, self.org, self.token)
            ready = readiness(raw)
            decision = self.controller.tick(raw, samples, ready, self.dry_run)
            with self.lock:
                self.metrics["spinr_burst_last_observation_timestamp_seconds"] = (
                    self.clock()
                )
                if decision.reason != "awaiting_fresh_sample":
                    self.metrics["spinr_burst_action_required"] = int(
                        decision.reason in ACTION_REQUIRED
                    )
                self.metrics["spinr_burst_last_failed_start_timestamp_seconds"] = (
                    self.controller.last_failed_at
                )
            logger.info(
                "Burst decision=%s mode=%s",
                decision.reason,
                "observe" if self.dry_run else "active",
            )
        except Exception as error:
            # Credential-bearing upstream errors and malformed JSON must not be logged verbatim.
            logger.error(
                "Burst cycle failed (%s); no immediate retry", type(error).__name__
            )
            self.controller.policy = Policy()
            with self.lock:
                self.metrics["spinr_burst_action_required"] = 1

    def exposition(self):
        with self.lock:
            return "".join(
                f"{name} {value}\n" for name, value in self.metrics.items()
            ).encode()


def serve_metrics(runtime):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/metrics":
                self.send_error(404)
                return
            body = runtime.exposition()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    class Server(HTTPServer):
        address_family = socket.AF_INET6

    server = Server(("::", 9090), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    runtime = Runtime(
        Fly(os.environ["BACKEND_APP"], os.environ["FLY_API_TOKEN"]),
        os.environ["BURST_ANCHOR_ID"],
        os.environ["BACKEND_APP"],
        os.environ["FLY_ORG"],
        os.environ["PROMETHEUS_TOKEN"],
        dry_run_setting(os.getenv("BURST_DRY_RUN", "true")),
    )
    serve_metrics(runtime)
    while True:
        started = time.monotonic()
        runtime.cycle()
        time.sleep(max(1, 15 - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
