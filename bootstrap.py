#!/usr/bin/env python3
"""Verify a signed V9 payload, then run exactly one registered base probe."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

TRUSTED_PUBLIC_KEY = Path("/trust/v9-grounded.pub")
VERIFIED_ROOT = Path("/opt/v9_verified")
INPUT_ROOT = Path("/inputs")
REPORT_PATH = Path("/tmp/frozen_eval_report.json")
RUNNER_UID = 65532
RUNNER_GID = 65532


def fail(message: str) -> None:
    raise SystemExit(f"V9 bootstrap rejected execution: {message}")


def safe_extract(payload: Path, destination: Path) -> None:
    with tarfile.open(payload, "r:") as archive:
        members = archive.getmembers()
        for member in members:
            target = (destination / member.name).resolve()
            if not target.is_relative_to(destination.resolve()):
                fail(f"payload member escapes extraction root: {member.name}")
            if member.issym() or member.islnk() or not (member.isdir() or member.isreg()):
                fail(f"payload member type is forbidden: {member.name}")
        archive.extractall(destination, members=members, filter="data")


def freeze_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir():
            path.chmod(0o555)
        else:
            path.chmod(0o444)
    root.chmod(0o555)


def verify_payload(payload: Path, bundle: Path) -> None:
    if not payload.is_file() or not bundle.is_file():
        fail("signed payload or its signature bundle is absent")
    checked = subprocess.run(
        ["/usr/local/bin/cosign", "verify-blob", "--key", str(TRUSTED_PUBLIC_KEY), "--bundle", str(bundle), str(payload)],
        capture_output=True,
        text=True,
    )
    if checked.returncode != 0:
        fail("payload signature verification failed")


def load_run_contract(root: Path) -> dict:
    path = root / "run_contract.json"
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"invalid signed run contract: {error}")
    if not isinstance(contract, dict) or contract.get("schema_version") != "v9-signed-probe-run-v1":
        fail("signed run contract schema is unsupported")
    if contract.get("generation") != {"max_new_tokens": 256, "do_sample": False}:
        fail("signed run contract must fix generation to 256 deterministic tokens")
    if not isinstance(contract.get("probes"), dict) or not contract["probes"]:
        fail("signed run contract has no registered probes")
    return contract


def prepare_runner_home() -> None:
    home = Path("/tmp/v9-home")
    home.mkdir(mode=0o700, exist_ok=True)
    cache = home / ".cache"
    cache.mkdir(mode=0o700, exist_ok=True)
    if os.geteuid() == 0:
        os.chown(home, RUNNER_UID, RUNNER_GID)
        os.chown(cache, RUNNER_UID, RUNNER_GID)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=Path("/signed/v9_payload.tar"))
    parser.add_argument("--signature-bundle", type=Path, default=Path("/signed/v9_payload.sigstore.json"))
    parser.add_argument("--probe", required=True)
    args = parser.parse_args()

    if os.environ.get("PYTHONPYCACHEPREFIX"):
        fail("PYTHONPYCACHEPREFIX is forbidden")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    verify_payload(args.package, args.signature_bundle)
    if VERIFIED_ROOT.exists():
        shutil.rmtree(VERIFIED_ROOT)
    VERIFIED_ROOT.mkdir(mode=0o700, parents=True)
    safe_extract(args.package, VERIFIED_ROOT)
    freeze_tree(VERIFIED_ROOT)
    contract = load_run_contract(VERIFIED_ROOT)
    probe = contract["probes"].get(args.probe)
    if not isinstance(probe, dict):
        fail("requested probe is not registered in the signed run contract")
    required = ("model_id", "revision", "report_repo")
    if any(not isinstance(probe.get(field), str) or not probe[field] for field in required):
        fail("registered probe is incomplete")

    evaluator = VERIFIED_ROOT / "runtime/scripts/eval_grounded_hf.py"
    execution_manifest = VERIFIED_ROOT / "contracts/execution_manifest.json"
    authorization_manifest = VERIFIED_ROOT / "contracts/authorization_manifest.json"
    if not all(path.is_file() for path in (evaluator, execution_manifest, authorization_manifest)):
        fail("signed payload is missing a required evaluator or contract file")
    prepare_runner_home()
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPYCACHEPREFIX"}
    env.update(
        {
            "HF_GROUNDED_PROJECT_ROOT": str(VERIFIED_ROOT / "runtime"),
            "HOME": "/tmp/v9-home",
            "PYTHONDONTWRITEBYTECODE": "1",
            "UV_CACHE_DIR": "/tmp/v9-home/.cache/uv",
        }
    )
    command = [
        "/usr/local/bin/uv",
        "run",
        "--python",
        "/usr/local/bin/python",
        str(evaluator),
        "--base-model",
        probe["model_id"],
        "--base-revision",
        probe["revision"],
        "--report-repo",
        probe["report_repo"],
        "--execution-manifest",
        str(execution_manifest),
        "--authorization-manifest",
        str(authorization_manifest),
        "--cases",
        str(INPUT_ROOT / "grounded/frozen_eval.jsonl"),
        "--dataset-manifest",
        str(INPUT_ROOT / "grounded/training/dataset_manifest.json"),
        "--index",
        str(INPUT_ROOT / "index/retrieval_index.jsonl"),
        "--source-manifest",
        str(INPUT_ROOT / "index/source_manifest.jsonl"),
        "--extracted-dir",
        str(INPUT_ROOT / "extracted"),
        "--max-new-tokens",
        "256",
        "--json-output",
        str(REPORT_PATH),
    ]
    if os.geteuid() == 0:
        os.setgroups([])
        os.setgid(RUNNER_GID)
        os.setuid(RUNNER_UID)
    os.execvpe(command[0], command, env)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
