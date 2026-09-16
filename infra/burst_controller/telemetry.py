"""Fly platform pressure and target-specific private readiness probes."""

from concurrent.futures import ThreadPoolExecutor
import ipaddress
import json
import math
from urllib.parse import quote, urlencode
from api import RemoteError, request
from policy import Sample


def values(result):
    try:
        if (
            result["status"] != "success"
            or result.get("warnings")
            or result["data"]["resultType"] != "vector"
        ):
            raise ValueError("Incomplete metrics response")
        output = {}
        for item in result["data"]["result"]:
            key, value = item["metric"]["instance"], float(item["value"][1])
            if not key or key in output or not math.isfinite(value):
                raise ValueError("Invalid or duplicate metric")
            output[key] = value
        return output
    except (KeyError, TypeError, IndexError):
        raise ValueError("Malformed metrics response") from None


def collect(app, org, token):
    a = json.dumps(app)
    selector = "{app=" + a + "}"
    memory = (
        "max by(instance)(1 - fly_instance_memory_mem_available"
        + selector
        + " / fly_instance_memory_mem_total"
        + selector
        + ")"
    )
    # Normalize busy CPU to the paid shared-CPU baseline, not burst credits.
    cpu = (
        "clamp_max(sum by(instance)(rate(fly_instance_cpu{app="
        + a
        + ',mode=~"user|nice|system|irq|softirq"}[1m])) / 100 / max by(instance)(fly_instance_cpu_baseline'
        + selector
        + "), 1)"
    )
    stamp = (
        "min by(instance)(timestamp({app="
        + a
        + ',__name__=~"fly_instance_memory_mem_available|fly_instance_memory_mem_total|fly_instance_cpu|fly_instance_cpu_baseline"}))'
    )
    url = "https://api.fly.io/prometheus/" + quote(org, safe="") + "/api/v1/query?"
    results = [
        values(request(url + urlencode({"query": q}), token))
        for q in (memory, cpu, stamp)
    ]
    mem, cpu, stamps = results
    if not mem or set(mem) != set(cpu) or set(mem) != set(stamps):
        raise ValueError("Missing or partial metrics")
    return {key: Sample(mem[key], cpu[key], stamps[key]) for key in mem}


def readiness(raw):
    targets = []
    for machine in raw:
        if machine["state"] != "started":
            continue
        address = ipaddress.IPv6Address(machine["private_ip"])
        if address not in ipaddress.IPv6Network("fdaa::/16"):
            raise ValueError("Readiness target is outside Fly private network")
        targets.append((machine["id"], f"http://[{address}]:8000/ready"))

    def probe(target):
        try:
            result = request(target[1])
            return (
                target[0]
                if result.get("status") == "ready"
                and result.get("db", {}).get("status") == "ok"
                else None
            )
        except RemoteError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        return {key for key in pool.map(probe, targets) if key is not None}
