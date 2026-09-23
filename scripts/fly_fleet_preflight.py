"""Read-only deploy guard; consumes `fly machine list --json` on stdin.

Allow filling missing capacity, never a count operation that removes Machines.
Run before deploy: adding a missing process group to eight legacy app Machines
would exceed the budget before a post-deploy scale command could correct it.
"""

import argparse
import json
import sys
from collections import Counter


def validate(machines, workers=0):
    if workers not in (0, 1):
        raise ValueError("Unsupported worker profile")
    if not isinstance(machines, list):
        raise ValueError("Expected a Machine list")
    counts = Counter()
    ids = set()
    for machine in machines:
        try:
            machine_id = machine["id"]
            region = machine["region"]
            group = machine["config"]["metadata"]["fly_process_group"]
        except (KeyError, TypeError):
            raise ValueError("Incomplete Machine inventory") from None
        if not isinstance(machine_id, str) or not machine_id or machine_id in ids:
            raise ValueError("Missing or duplicate Machine ID")
        allowed_groups = ("app", "burst", "worker") if workers else ("app", "burst")
        if region != "yyz" or group not in allowed_groups:
            raise ValueError("Unexpected region or process group")
        ids.add(machine_id)
        counts[group] += 1
    burst_limit = 5 if workers else 6
    if (
        counts["app"] > 2
        or counts["burst"] > burst_limit
        or counts["worker"] > workers
        or sum(counts.values()) > 8
    ):
        raise ValueError("Fleet needs migration; see docs/runbooks/fly-mixed-fleet.md")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check the current Fly Machine inventory")
    parser.add_argument(
        "--workers",
        type=int,
        choices=(1,),
        default=0,
        help="opt into the worker canary budget (app<=2, burst<=5, worker<=1)",
    )
    args = parser.parse_args(argv)
    try:
        validate(json.load(sys.stdin), workers=args.workers)
    except (ValueError, TypeError):
        # Never echo raw CLI input: it can contain runtime configuration.
        sys.stderr.write(
            "Fly fleet preflight failed. Verify inventory and follow "
            "docs/runbooks/fly-mixed-fleet.md before deploying.\n"
        )
        return 1
    if args.workers:
        sys.stdout.write(
            "Fly fleet preflight passed: safe to fill app=2 burst=5 worker=1 in yyz.\n"
        )
    else:
        sys.stdout.write("Fly fleet preflight passed: safe to fill app=2 burst=6 in yyz.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
