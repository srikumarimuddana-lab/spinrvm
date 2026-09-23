"""Validate and record non-secret rollback evidence for the staging Fly app."""

import argparse
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path


STAGING_APP = "spinr-backend-staging"
STAGING_REGION = "yyz"
STAGING_REGISTRY = "registry.fly.io"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class RecoveryDenied(ValueError):
    """The staging deployment has no verified rollback baseline."""


def _config_hash(config_toml):
    if not isinstance(config_toml, str) or not config_toml.strip():
        raise RecoveryDenied("staging config is missing")
    try:
        parsed = tomllib.loads(config_toml)
    except tomllib.TOMLDecodeError:
        raise RecoveryDenied("staging config is invalid TOML") from None
    if not isinstance(parsed, dict) or parsed.get("app") != STAGING_APP:
        raise RecoveryDenied("config does not target staging")
    env = parsed.get("env")
    if not isinstance(env, dict) or env.get("ENV") != "staging":
        raise RecoveryDenied("config is not staging mode")
    return hashlib.sha256(config_toml.encode("utf-8")).hexdigest()


def capture_snapshot(machines, deploy_info, config_toml):
    """Return sanitized evidence, or None for an empty app.

    The caller must load the config from ``backend/fly.staging.toml`` at the
    served SHA, then verify this snapshot hash before any mutation or restore.
    The known nonsecret config file is kept separate from this JSON metadata.
    """
    config_hash = _config_hash(config_toml)
    if not isinstance(machines, list):
        raise RecoveryDenied("machine inventory is not a list")
    if not machines:
        return None
    if len(machines) != 1 or not isinstance(machines[0], dict):
        raise RecoveryDenied("staging inventory is not a single machine")

    machine = machines[0]
    try:
        machine_id = machine["id"]
        region = machine["region"]
        state = machine["state"]
        group = machine["config"]["metadata"]["fly_process_group"]
        image = machine["image_ref"]
        registry, repository, digest = image["registry"], image["repository"], image["digest"]
    except (KeyError, TypeError):
        raise RecoveryDenied("staging machine image evidence is incomplete") from None
    if not isinstance(machine_id, str) or not machine_id:
        raise RecoveryDenied("staging machine ID is unavailable")
    if region != STAGING_REGION or state != "started" or group != "app":
        raise RecoveryDenied("staging machine is not ready for a snapshot")
    if registry != STAGING_REGISTRY or repository != STAGING_APP:
        raise RecoveryDenied("image is not the staging app image")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise RecoveryDenied("staging image digest is not immutable")

    if not isinstance(deploy_info, dict) or deploy_info.get("provider") != "fly" or deploy_info.get("env") != "staging":
        raise RecoveryDenied("served build is not the staging app")
    build = deploy_info.get("build")
    served_sha = build.get("sha") if isinstance(build, dict) else None
    if not isinstance(served_sha, str) or not _SHA.fullmatch(served_sha):
        raise RecoveryDenied("served staging SHA is unavailable")

    image_ref = f"{registry}/{repository}@{digest}"
    return {
        "schema_version": 1,
        "app": STAGING_APP,
        "region": STAGING_REGION,
        "process_group": "app",
        "image_digest": digest,
        "image_ref": image_ref,
        "served_sha": served_sha,
        "config_commit_sha": served_sha,
        "config_toml_sha256": config_hash,
    }


def validate_snapshot(snapshot, config_toml):
    """Validate recovery metadata against its separately stored config file."""
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        raise RecoveryDenied("snapshot schema is unsupported")
    if snapshot.get("app") != STAGING_APP or snapshot.get("region") != STAGING_REGION or snapshot.get("process_group") != "app":
        raise RecoveryDenied("snapshot is not scoped to staging")
    digest = snapshot.get("image_digest")
    image_ref = snapshot.get("image_ref")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest) or not isinstance(image_ref, str):
        raise RecoveryDenied("snapshot image evidence is invalid")
    image_path, separator, image_digest = image_ref.rpartition("@")
    if not separator or image_path != f"{STAGING_REGISTRY}/{STAGING_APP}" or image_digest != digest:
        raise RecoveryDenied("snapshot image reference is not digest-pinned")
    sha = snapshot.get("served_sha")
    if not isinstance(sha, str) or not _SHA.fullmatch(sha) or snapshot.get("config_commit_sha") != sha:
        raise RecoveryDenied("snapshot build/config SHA is invalid")
    config_hash = _config_hash(config_toml)
    if snapshot.get("config_toml_sha256") != config_hash:
        raise RecoveryDenied("snapshot config hash does not match")
    return config_hash


def _main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True)
    parser.add_argument("--machines", required=True)
    parser.add_argument("--deploy-info", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.app != STAGING_APP:
            raise RecoveryDenied("refusing a non-staging app")
        machines = json.loads(Path(args.machines).read_text(encoding="utf-8"))
        deploy_info = json.loads(Path(args.deploy_info).read_text(encoding="utf-8"))
        config_toml = Path(args.config).read_text(encoding="utf-8")
        snapshot = capture_snapshot(machines, deploy_info, config_toml)
        if snapshot is None:
            Path(args.output).unlink(missing_ok=True)
            print("Staging recovery baseline: empty app (bootstrap)")
            return 0
        Path(args.output).write_text(json.dumps(snapshot, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Staging recovery baseline verified: {snapshot['served_sha']} {snapshot['image_digest']}")
        return 0
    except (OSError, json.JSONDecodeError, RecoveryDenied):
        print("Staging recovery snapshot denied: incomplete or invalid baseline evidence.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(_main())
