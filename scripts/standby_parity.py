#!/usr/bin/env python3
"""Compare the Fly primary and the Railway warm standby; emit findings.

Runs inside .github/workflows/standby-parity-monitor.yml, after the workflow
has collected raw evidence into files (it never sees a secret VALUE — only
variable names, HTTP status codes, and the HMAC fingerprints /deploy-info
returns). Pure stdlib so it can be unit-tested locally without the backend's
dependencies:

    python3 scripts/standby_parity.py --self-test

What it decides (ADR-007's standby invariants, ACTION_ITEMS.md C5):
  1. Railway deploy token still works (else no deploy has been landing).
  2. Every required variable name (deploy/backend-required-env.txt) is present
     on each provider, per scope.
  3. Variables present on only one provider (secret drift in either direction).
  4. Both /health endpoints answer 200.
  5. Both providers serve the SAME build sha, and Fly serves main's HEAD.
  6. Every config fingerprint matches across providers (value parity), with
     an all-rows-differ result diagnosed as a JWT_SECRET mismatch.

Severity: CRITICAL = fail-over today would be broken or unsafe; WARN = could
not verify, or a non-blocking drift; OK = nothing to report.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

CRITICAL, WARN, OK = "CRITICAL", "WARN", "OK"

# Names injected by the platforms themselves or supplied by fly.toml [env];
# their presence on one provider only is expected, not drift.
_PLATFORM_PREFIXES = ("RAILWAY_", "FLY_")
_PLATFORM_NAMES = {
    # fly.toml [env] — Fly has them as env, Railway as variables (scope=railway)
    "ENV",
    "PORT",
    "SUPABASE_REGION",
    "UVICORN_WORKERS",
    "PYTHONUNBUFFERED",
    # `flyctl secrets list` never returns a Fly *env* var, so these show up on
    # Railway only by construction.
}


def load_required(path: Path) -> dict[str, str]:
    """{NAME: scope} from deploy/backend-required-env.txt."""
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2 or parts[1] not in ("both", "railway"):
            raise SystemExit(f"{path}: malformed line {raw!r} (expected 'NAME both|railway')")
        out[parts[0]] = parts[1]
    return out


def _read_json(path: str | None):
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return {"__unparseable__": True}


class Report:
    def __init__(self) -> None:
        self.sections: dict[str, list[tuple[str, str]]] = {}
        self.worst = OK

    def add(self, section: str, level: str, text: str) -> None:
        self.sections.setdefault(section, []).append((level, text))
        if level == CRITICAL or (level == WARN and self.worst == OK):
            self.worst = level

    def markdown(self) -> str:
        icon = {CRITICAL: "🔴 CRITICAL", WARN: "🟡 WARN", OK: "🟢 OK"}
        lines: list[str] = []
        for section, items in self.sections.items():
            lines.append(f"### {section}")
            for level, text in items:
                lines.append(f"- {icon[level]} — {text}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def evaluate(
    *,
    required: dict[str, str],
    railway_names,
    railway_error: str | None,
    fly_names,
    fly_error: str | None,
    railway_health: str,
    fly_health: str,
    railway_info,
    fly_info,
    main_sha: str | None,
) -> Report:
    r = Report()
    # A listing that never arrived (file missing, no error captured) must read
    # as a failure, never as "no names present".
    if railway_names is None and not railway_error:
        railway_error = "listing not available (collection step produced no output)"
    if fly_names is None and not fly_error:
        fly_error = "listing not available (collection step produced no output)"

    # ── 1 + 2: Railway token and variable presence ──────────────────────────
    sec = "Railway variables"
    if railway_error:
        r.add(
            sec,
            CRITICAL,
            f"could not list Railway variables: `{railway_error}`. If this says the token is invalid, "
            "**no Railway deploy has been landing** — rotate RAILWAY_TOKEN (Project Token) in GitHub secrets.",
        )
        railway_names = None
    else:
        missing = [n for n, scope in required.items() if scope in ("both", "railway") and n not in railway_names]
        if missing:
            r.add(
                sec,
                CRITICAL,
                "missing on Railway: " + ", ".join(f"`{n}`" for n in missing) + ". Set in Railway → service → "
                "Variables. `ENV`/`SUPABASE_REGION` missing means the standby boots in dev mode.",
            )
        else:
            r.add(sec, OK, f"all {sum(1 for s in required.values() if s in ('both', 'railway'))} required names present")

    sec = "Fly secrets"
    if fly_error:
        r.add(sec, CRITICAL, f"could not list Fly secrets: `{fly_error}`")
        fly_names = None
    else:
        missing = [n for n, scope in required.items() if scope == "both" and n not in fly_names]
        if missing:
            r.add(sec, CRITICAL, "missing on Fly (the PRIMARY): " + ", ".join(f"`{n}`" for n in missing))
        else:
            r.add(sec, OK, f"all {sum(1 for s in required.values() if s == 'both')} required names present")

    # ── 3: one-sided extras ──────────────────────────────────────────────────
    sec = "Variables present on one provider only"
    if railway_names is not None and fly_names is not None:

        def _interesting(n: str) -> bool:
            return not n.startswith(_PLATFORM_PREFIXES) and n not in _PLATFORM_NAMES

        only_rw = sorted(n for n in set(railway_names) - set(fly_names) if _interesting(n))
        only_fly = sorted(n for n in set(fly_names) - set(railway_names) if _interesting(n))
        if only_rw:
            r.add(sec, WARN, "Railway only: " + ", ".join(f"`{n}`" for n in only_rw))
        if only_fly:
            r.add(sec, WARN, "Fly only: " + ", ".join(f"`{n}`" for n in only_fly) + " — a feature configured on the primary is silently off on the standby")
        if not only_rw and not only_fly:
            r.add(sec, OK, "none (excluding platform-injected `FLY_*`/`RAILWAY_*` and fly.toml `[env]` names)")
    else:
        r.add(sec, WARN, "not compared (one side's listing failed)")

    # ── 4: health ────────────────────────────────────────────────────────────
    sec = "Health"
    for label, code in (("Fly", fly_health), ("Railway", railway_health)):
        if code == "skipped":
            r.add(sec, WARN, f"{label} `/health` not probed (its *_HEALTH_URL secret is unset)")
        elif code == "200":
            r.add(sec, OK, f"{label} `/health` → 200")
        else:
            r.add(sec, CRITICAL, f"{label} `/health` → {code} (503 = up but its DB check failed; 000 = unreachable)")

    # ── 5 + 6: build sha and value parity ────────────────────────────────────
    sec = "Build + config parity (`/deploy-info`)"

    def _usable(info) -> bool:
        return isinstance(info, dict) and "fingerprints" in info and "__unparseable__" not in info

    if not _usable(fly_info) or not _usable(railway_info):
        why = []
        for label, info in (("Fly", fly_info), ("Railway", railway_info)):
            if info is None:
                why.append(f"{label}: not fetched (needs `METRICS_AUTH_TOKEN` + health URL secrets, and the backend "
                           "must have `METRICS_AUTH_TOKEN` set — otherwise `/deploy-info` answers 503)")
            elif not _usable(info):
                why.append(f"{label}: unusable response ({info.get('__error__', 'not JSON')})")
        r.add(sec, WARN, "value parity NOT verified — " + "; ".join(why))
        return r

    fb, rb = fly_info.get("build") or {}, railway_info.get("build") or {}
    fsha, rsha = fb.get("sha"), rb.get("sha")
    if not fsha or not rsha:
        r.add(
            sec,
            WARN,
            f"build stamp missing (Fly: `{fsha or 'none'}`, Railway: `{rsha or 'none'}`) — at least one provider "
            "is running an image built before the deploy workflows started stamping `build_info.json`",
        )
    elif fsha != rsha:
        r.add(
            sec,
            CRITICAL,
            f"**standby is on a different build**: Fly `{fsha[:12]}` vs Railway `{rsha[:12]}` "
            f"(Railway built {rb.get('built_at') or '?'}). Fail-over would land on stale code.",
        )
    else:
        r.add(sec, OK, f"both serve `{fsha[:12]}`")
    if main_sha and fsha and fsha != main_sha:
        r.add(sec, WARN, f"Fly serves `{fsha[:12]}` but `main` is `{main_sha[:12]}` — a deploy may be in flight or failed")

    for label, info, expect in (("Fly", fly_info, "fly"), ("Railway", railway_info, "railway")):
        if info.get("provider") != expect:
            r.add(sec, CRITICAL, f"the `{label}` URL is served by provider `{info.get('provider')}` — is the health URL secret pointing at the right host?")
        if info.get("env") != "production":
            r.add(sec, CRITICAL, f"{label} reports `ENV={info.get('env')}` — production guards are OFF there")

    ff, rf = fly_info["fingerprints"], railway_info["fingerprints"]
    names = sorted(set(ff) | set(rf))
    set_both = [n for n in names if ff.get(n) is not None and rf.get(n) is not None]
    differing = [n for n in set_both if ff[n] != rf[n]]
    one_sided = [n for n in names if (ff.get(n) is None) != (rf.get(n) is None)]

    if set_both and len(differing) == len(set_both):
        r.add(
            sec,
            CRITICAL,
            "**every fingerprint differs → `JWT_SECRET` is not identical across providers** (it keys the HMAC). "
            "Users would be logged out at random after a fail-over. Fix JWT_SECRET first, then re-run.",
        )
    elif differing:
        r.add(sec, CRITICAL, "values DIFFER across providers: " + ", ".join(f"`{n}`" for n in differing))
    if one_sided:
        parts = []
        for n in one_sided:
            side = "Fly" if ff.get(n) is None else "Railway"
            parts.append(f"`{n}` (unset on {side})")
        r.add(sec, CRITICAL if any(n in required for n in one_sided) else WARN, "set on one provider only: " + ", ".join(parts))
    if not differing and not one_sided:
        r.add(sec, OK, f"all {len(set_both)} set fields identical, {len(names) - len(set_both)} unset on both")
    return r


def _write_outputs(report: Report) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    with open(out, "a", encoding="utf-8") as fh:
        fh.write(f"severity={report.worst}\n")
        fh.write("findings<<PARITY_EOF\n")
        fh.write(report.markdown())
        fh.write("PARITY_EOF\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--required", default="deploy/backend-required-env.txt")
    ap.add_argument("--railway-names", help="JSON array of variable names on the Railway service")
    ap.add_argument("--railway-error", default="", help="error text if listing Railway variables failed")
    ap.add_argument("--fly-names", help="JSON array of secret names on the Fly app")
    ap.add_argument("--fly-error", default="")
    ap.add_argument("--railway-health", default="skipped", help="HTTP status of Railway /health, or 'skipped'")
    ap.add_argument("--fly-health", default="skipped")
    ap.add_argument("--railway-info", help="path to Railway /deploy-info JSON (absent = not fetched)")
    ap.add_argument("--fly-info")
    ap.add_argument("--main-sha", default="")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    report = evaluate(
        required=load_required(Path(args.required)),
        railway_names=_read_json(args.railway_names),
        railway_error=args.railway_error or None,
        fly_names=_read_json(args.fly_names),
        fly_error=args.fly_error or None,
        railway_health=args.railway_health,
        fly_health=args.fly_health,
        railway_info=_read_json(args.railway_info),
        fly_info=_read_json(args.fly_info),
        main_sha=args.main_sha or None,
    )
    print(f"SEVERITY: {report.worst}\n")
    print(report.markdown())
    _write_outputs(report)
    return 0


# ── self-test (no pytest needed; run in CI via `--self-test`) ────────────────
def _self_test() -> int:
    req = {"JWT_SECRET": "both", "REDIS_URL": "both", "SENTRY_DSN": "both", "ENV": "railway", "SUPABASE_REGION": "railway"}
    fp = lambda **kw: {"ENV": "aa", "JWT_SECRET": "bb", "REDIS_URL": "cc", "OTP_PEPPER": None, **kw}  # noqa: E731
    info = lambda prov, sha="abc123456789abcdef", **kw: {  # noqa: E731
        "provider": prov, "env": "production", "build": {"sha": sha, "built_at": "t"}, "fingerprints": fp(**kw)
    }
    base = dict(
        required=req,
        railway_names=["JWT_SECRET", "REDIS_URL", "SENTRY_DSN", "ENV", "SUPABASE_REGION", "RAILWAY_PROJECT_ID", "PORT"],
        railway_error=None,
        fly_names=["JWT_SECRET", "REDIS_URL", "SENTRY_DSN"],
        fly_error=None,
        railway_health="200",
        fly_health="200",
        railway_info=info("railway"),
        fly_info=info("fly"),
        main_sha="abc123456789abcdef",
    )
    checks = 0

    def expect(level: str, needle: str, **override):
        nonlocal checks
        rep = evaluate(**{**base, **override})
        md = rep.markdown()
        assert rep.worst == level, f"{override}: expected {level}, got {rep.worst}\n{md}"
        assert needle in md, f"{override}: {needle!r} not in report\n{md}"
        checks += 1

    expect(OK, "both serve `abc123456789`")
    expect(CRITICAL, "no Railway deploy has been landing", railway_error="Invalid RAILWAY_TOKEN")
    expect(CRITICAL, "listing not available", railway_names=None)
    expect(CRITICAL, "could not list Fly secrets", fly_names=None)
    expect(CRITICAL, "missing on Railway: `ENV`", railway_names=["JWT_SECRET", "REDIS_URL", "SENTRY_DSN", "SUPABASE_REGION"])
    expect(CRITICAL, "missing on Fly (the PRIMARY): `SENTRY_DSN`", fly_names=["JWT_SECRET", "REDIS_URL"])
    expect(WARN, "Fly only: `ALERT_WEBHOOK_URL`", fly_names=["JWT_SECRET", "REDIS_URL", "SENTRY_DSN", "ALERT_WEBHOOK_URL"])
    expect(OK, "none (excluding", railway_names=base["railway_names"] + ["RAILWAY_ENVIRONMENT_ID", "UVICORN_WORKERS"])
    expect(CRITICAL, "Railway `/health` → 503", railway_health="503")
    expect(WARN, "Railway `/health` not probed", railway_health="skipped")
    expect(WARN, "value parity NOT verified", railway_info=None)
    expect(WARN, "unusable response", railway_info={"__unparseable__": True})
    expect(CRITICAL, "standby is on a different build", railway_info=info("railway", sha="fff0000000000000"))
    expect(WARN, "a deploy may be in flight", main_sha="0000000000000000")
    expect(WARN, "build stamp missing", railway_info={**info("railway"), "build": None})
    expect(CRITICAL, "served by provider `fly`", railway_info=info("fly"))
    expect(CRITICAL, "`ENV=development`", railway_info={**info("railway"), "env": "development"})
    expect(CRITICAL, "values DIFFER across providers: `REDIS_URL`", railway_info=info("railway", REDIS_URL="zz"))
    expect(CRITICAL, "`JWT_SECRET` is not identical", railway_info=info("railway", ENV="x", JWT_SECRET="y", REDIS_URL="z"))
    expect(CRITICAL, "`REDIS_URL` (unset on Railway)", railway_info=info("railway", REDIS_URL=None))
    expect(WARN, "`OTP_PEPPER` (unset on Fly)", railway_info=info("railway", OTP_PEPPER="dd"))
    print(f"standby_parity self-test: {checks} scenarios ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
