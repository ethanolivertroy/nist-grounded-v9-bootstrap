#!/bin/sh
# This file is base64-inlined by the trusted GitHub Action into the HF Job command.
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: v9_hf_job_bootstrap.sh <registered-probe-slug>" >&2
  exit 64
fi
probe="$1"
: "${V9_PUBLIC_KEY_B64:?missing trusted public key}"
: "${V9_PAYLOAD_SHA256:?missing trusted payload digest}"

if [ -n "${PYTHONPYCACHEPREFIX:-}" ]; then
  echo "V9 bootstrap rejects PYTHONPYCACHEPREFIX" >&2
  exit 65
fi
export PYTHONDONTWRITEBYTECODE=1
payload=/signed/v9_payload.tar
signature=/signed/v9_payload.tar.sig
[ -f "$payload" ] && [ -f "$signature" ] || { echo "signed payload is missing" >&2; exit 66; }
printf '%s' "$V9_PUBLIC_KEY_B64" | base64 -d > /tmp/v9-ed25519.pub
actual_payload_sha256="$(sha256sum "$payload" | cut -d' ' -f1)"
[ "$actual_payload_sha256" = "$V9_PAYLOAD_SHA256" ] || { echo "payload digest mismatch" >&2; exit 67; }
openssl pkeyutl -verify -rawin -pubin -inkey /tmp/v9-ed25519.pub -in "$payload" -sigfile "$signature" >/dev/null || { echo "payload signature verification failed" >&2; exit 68; }

verified_root=/opt/v9_verified
rm -rf "$verified_root"
mkdir -p "$verified_root"
tar --extract --file "$payload" --directory "$verified_root" --no-same-owner --no-same-permissions
[ ! -n "$(find "$verified_root" -type l -print -quit)" ] || { echo "verified payload contains a symlink" >&2; exit 69; }
find "$verified_root" -type d -exec chmod 0555 {} +
find "$verified_root" -type f -exec chmod 0444 {} +

read_probe_field() {
  field="$1"
  /usr/local/bin/python - "$probe" "$field" "$verified_root/run_contract.json" <<'PY'
import json
import sys
probe, field, path = sys.argv[1:]
contract = json.load(open(path, encoding="utf-8"))
if contract.get("schema_version") != "v9-signed-probe-run-v1":
    raise SystemExit("unsupported signed run contract")
if contract.get("generation") != {"max_new_tokens": 256, "do_sample": False}:
    raise SystemExit("signed generation contract is not deterministic-256")
entry = contract.get("probes", {}).get(probe)
if not isinstance(entry, dict) or not isinstance(entry.get(field), str):
    raise SystemExit("probe is not registered by the signed contract")
print(entry[field])
PY
}
model_id="$(read_probe_field model_id)"
revision="$(read_probe_field revision)"
report_repo="$(read_probe_field report_repo)"
mkdir -p /tmp/v9-home/.cache
chown -R 65532:65532 /tmp/v9-home
export HF_GROUNDED_PROJECT_ROOT="$verified_root/runtime"
export HOME=/tmp/v9-home
export UV_CACHE_DIR=/tmp/v9-home/.cache/uv
exec setpriv --reuid=65532 --regid=65532 --clear-groups --inh-caps=-all \
  /usr/local/bin/uv run --python /usr/local/bin/python "$verified_root/runtime/scripts/eval_grounded_hf.py" \
  --base-model "$model_id" \
  --base-revision "$revision" \
  --report-repo "$report_repo" \
  --execution-manifest "$verified_root/contracts/execution_manifest.json" \
  --authorization-manifest "$verified_root/contracts/authorization_manifest.json" \
  --cases /inputs/grounded/frozen_eval.jsonl \
  --dataset-manifest /inputs/grounded/training/dataset_manifest.json \
  --index /inputs/index/retrieval_index.jsonl \
  --source-manifest /inputs/index/source_manifest.jsonl \
  --extracted-dir /inputs/extracted \
  --max-new-tokens 256 \
  --json-output /tmp/frozen_eval_report.json
