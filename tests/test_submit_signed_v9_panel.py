import base64
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("submit_signed_v9_panel", ROOT / "submit_signed_v9_panel.py")
assert SPEC and SPEC.loader
submission = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(submission)


class SignedV9PanelTests(unittest.TestCase):
    def test_local_material_and_bootstrap_command_bind_the_real_public_key(self):
        public_key_b64, bootstrap_b64 = submission.require_local_material()
        command = submission.build_job_command("qwen25_32b_instruct", public_key_b64, bootstrap_b64)
        self.assertIn(f"V9_PUBLIC_KEY_B64={public_key_b64}", command)
        self.assertNotIn("$public_key_b64", command)
        self.assertEqual(base64.b64decode(public_key_b64), submission.PUBLIC_KEY.read_bytes())
        self.assertIn(submission.PAYLOAD_SHA256, command)

    def test_receipt_requires_the_signed_provider_identity(self):
        public_key_b64, bootstrap_b64 = submission.require_local_material()
        probe = "qwen25_32b_instruct"
        command = submission.build_job_command(probe, public_key_b64, bootstrap_b64)
        receipt = {
            "docker_image": submission.IMAGE,
            "flavor": "a100-large",
            "labels": {
                "v9-job-name": "v9-qwen25_32b_instruct-signed-base-probe",
                "v9-panel": submission.PANEL_LABEL,
                "v9-probe": probe,
            },
            "command": ["/bin/sh"],
            "arguments": ["-c", command],
            "secrets": {"HF_TOKEN": None},
            "volumes": [
                {"mount_path": "/signed", "type": "bucket"},
                {"mount_path": "/inputs", "type": "dataset", "name": "ethanolivertroy/v9-grounded-eval-inputs", "revision": "cb16deb80345dc94c94a41e89ed07e822ac8bf7a"},
            ],
        }
        submission.validate_job_receipt(receipt, probe=probe, job_command=command)
        receipt["flavor"] = "cpu-basic"
        with self.assertRaisesRegex(RuntimeError, "hardware"):
            submission.validate_job_receipt(receipt, probe=probe, job_command=command)


if __name__ == "__main__":
    unittest.main()
