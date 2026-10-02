#!/usr/bin/env python3
"""Submit exactly the signed V9 base-probe panel after an Environment approval."""
from __future__ import annotations

import argparse
import base64
from dataclasses import asdict
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parent
PAYLOAD = ROOT / ".v9_payload/v9_payload.tar"
SIGNATURE = ROOT / ".v9_payload/v9_payload.tar.sig"
PUBLIC_KEY = ROOT / "trust/v9-package-ed25519.pub"
PAYLOAD_SHA256 = "9fab2a347fc9e97cb348a0ee492ba5e5913ace927b10df60b0582a54a78ab6d7"
PUBLIC_KEY_SHA256 = "40e2e00f2f9093f92f11888a347ae5c16ce5852940bcefa42c912d875b5c7cc4"
INPUTS_VOLUME = "hf://datasets/ethanolivertroy/v9-grounded-eval-inputs@cb16deb80345dc94c94a41e89ed07e822ac8bf7a:/inputs:ro"
IMAGE = "ghcr.io/astral-sh/uv@sha256:85d4cb1afa769a7338e095b927bee941cf5ec92266c7424b3f6c0f2748567248"
PANEL_LABEL = "v9-base-panel-20261002"
PROBES = ("qwen25_32b_instruct", "qwen25_coder_32b", "gpt_oss_20b")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command: list[str]) -> str:
    completed = subprocess.run(command, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return completed.stdout.strip()


def require_local_material() -> tuple[str, str]:
    if sha256(PAYLOAD) != PAYLOAD_SHA256:
        raise RuntimeError("signed payload digest does not match the immutable submission constant")
    if sha256(PUBLIC_KEY) != PUBLIC_KEY_SHA256:
        raise RuntimeError("package public-key digest does not match the immutable submission constant")
    if not SIGNATURE.is_file() or SIGNATURE.stat().st_size == 0:
        raise RuntimeError("detached package signature is missing")
    subprocess.run(
        ["openssl", "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", str(PUBLIC_KEY), "-in", str(PAYLOAD), "-sigfile", str(SIGNATURE)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    public_key_b64 = base64.b64encode(PUBLIC_KEY.read_bytes()).decode("ascii")
    bootstrap_b64 = base64.b64encode((ROOT / "v9_hf_job_bootstrap.sh").read_bytes()).decode("ascii")
    return public_key_b64, bootstrap_b64


def build_job_command(probe: str, public_key_b64: str, bootstrap_b64: str) -> str:
    if probe not in PROBES:
        raise RuntimeError("unregistered V9 probe")
    parts = [
        "umask 077",
        f"printf %s {shlex.quote(bootstrap_b64)} | base64 -d > /tmp/v9-bootstrap",
        "chmod 0700 /tmp/v9-bootstrap",
        f"env V9_PUBLIC_KEY_B64={shlex.quote(public_key_b64)} V9_PAYLOAD_SHA256={PAYLOAD_SHA256} /bin/sh /tmp/v9-bootstrap {shlex.quote(probe)}",
    ]
    return "; ".join(parts)


def existing_panel_jobs(token: str):
    return [
        job
        for job in HfApi(token=token).list_jobs()
        if (job.labels or {}).get("v9-panel") == PANEL_LABEL
        or str((job.labels or {}).get("v9-job-name", "")).startswith("v9-")
    ]


def receipt_json(receipt: dict) -> str:
    return json.dumps(receipt, indent=2, sort_keys=True, default=str) + "\n"


def validate_job_receipt(job: dict, *, probe: str, job_command: str) -> None:
    if job.get("docker_image") != IMAGE or job.get("flavor") != "a100-large":
        raise RuntimeError("submitted job image or hardware does not match the signed panel")
    labels = job.get("labels") or {}
    expected_name = f"v9-{probe}-signed-base-probe"
    if labels.get("v9-job-name") != expected_name or labels.get("v9-panel") != PANEL_LABEL or labels.get("v9-probe") != probe:
        raise RuntimeError("submitted job identity labels do not match the signed panel")
    if job.get("command") != ["/bin/sh"] or job.get("arguments") != ["-c", job_command]:
        raise RuntimeError("submitted job command does not match the signed bootstrap command")
    if "HF_TOKEN" not in (job.get("secrets") or {}):
        raise RuntimeError("submitted job lacks the required encrypted HF_TOKEN secret")
    serialized_volumes = json.dumps(job.get("volumes") or [], sort_keys=True)
    if "/signed" not in serialized_volumes or "/inputs" not in serialized_volumes:
        raise RuntimeError("submitted job is missing a required read-only volume")
    if "ethanolivertroy/v9-grounded-eval-inputs" not in serialized_volumes or "cb16deb80345dc94c94a41e89ed07e822ac8bf7a" not in serialized_volumes:
        raise RuntimeError("submitted job input dataset does not retain its immutable revision")


def submit_probe(probe: str, public_key_b64: str, bootstrap_b64: str, receipts: Path) -> None:
    job_command = build_job_command(probe, public_key_b64, bootstrap_b64)
    job_name = f"v9-{probe}-signed-base-probe"
    output = run([
        "hf", "--format", "quiet", "jobs", "run", "--detach", "--flavor", "a100-large", "--timeout", "30m",
        "--name", job_name, "--label", f"v9-job-name={job_name}", "--label", f"v9-panel={PANEL_LABEL}", "--label", f"v9-probe={probe}",
        "--label", f"v9-payload-sha256={PAYLOAD_SHA256}", "--label", "v9-internal-timeout-seconds=1740",
        "--secrets", "HF_TOKEN", "-v", ".v9_payload:/signed:ro", "-v", INPUTS_VOLUME, IMAGE, "/bin/sh", "-c", job_command,
    ])
    job_id = output.splitlines()[-1].strip()
    if not re.fullmatch(r"[0-9a-f]{24}", job_id):
        raise RuntimeError("HF CLI quiet result was not an exact job ID")
    try:
        receipt = asdict(HfApi(token=os.environ["HF_TOKEN"]).inspect_job(job_id=job_id))
        validate_job_receipt(receipt, probe=probe, job_command=job_command)
        serialized_receipt = receipt_json(receipt)
    except Exception:
        subprocess.run(["hf", "jobs", "cancel", job_id], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        raise
    (receipts / f"{probe}.json").write_text(serialized_receipt)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-local", action="store_true")
    args = parser.parse_args()
    public_key_b64, bootstrap_b64 = require_local_material()
    if args.validate_local:
        summary = {probe: hashlib.sha256(build_job_command(probe, public_key_b64, bootstrap_b64).encode()).hexdigest() for probe in PROBES}
        print(json.dumps({"local_material": "valid", "probe_command_sha256": summary}, sort_keys=True))
        return 0
    if not os.environ.get("HF_TOKEN", "").startswith("hf_"):
        raise RuntimeError("protected Environment did not provide a Hugging Face token")
    token = os.environ["HF_TOKEN"]
    if existing_panel_jobs(token):
        raise RuntimeError("a V9 panel job already exists; a new signed package is required before any replay")
    receipts = ROOT / "submission-receipts"
    receipts.mkdir(exist_ok=False)
    for probe in PROBES:
        submit_probe(probe, public_key_b64, bootstrap_b64, receipts)
    print(json.dumps({"submitted": list(PROBES), "receipt_directory": str(receipts)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
