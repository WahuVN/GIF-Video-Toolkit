"""Synthetic offline release-gate tests; fixtures never touch the real repository's Git/index."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_media_release125.py"
spec = importlib.util.spec_from_file_location("check_media_release125", SCRIPT)
gate = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(gate)


class MediaReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ghmedia125-fixture-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "candidate"
        self.root.mkdir()
        init = subprocess.run(["git", "init", "-q", str(self.root)], capture_output=True, timeout=15)
        if init.returncode:
            self.skipTest("git unavailable for synthetic fixture")
        self.put("README.md", "# Synthetic media project\n")
        self.put("requirements.txt", "imageio-ffmpeg>=0.5.1\n")

    def put(self, name: str, content: str | bytes):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
        return target

    def test_missing_root_license_fail_closed(self):
        report = gate.check(self.root)
        self.assertEqual(report["decision"], "HOLD")
        self.assertFalse(report["release_authorized"])
        self.assertIn("ROOT_PROJECT_LICENSE_MISSING", report["reasons"])
        self.assertIn("OWNER_DISTRIBUTION_AUTHORITY_UNVERIFIED", report["reasons"])
        self.assertIn("IMAGEIO_FFMPEG_ATTRIBUTION_MISSING", report["reasons"])

    def test_binary_absent_is_not_confused_with_runtime_dependency(self):
        report = gate.check(self.root)
        self.assertEqual(report["bundled_ffmpeg"], "not_detected_in_candidate")
        self.assertEqual(report["counts"]["bundled_ffmpeg"], 0)
        self.assertNotIn("BUNDLED_FFMPEG_LICENSE_EVIDENCE_MISSING", report["reasons"])
        self.assertIn("FFMPEG_EXECUTABLE_SOURCE_BUILD_CONFIGURATION_UNVERIFIED", report["reasons"])
        self.assertEqual(report["decision"], "HOLD")

    def test_bundled_binary_requires_build_provenance_and_license_evidence(self):
        self.put("bin/ffmpeg.exe", b"synthetic executable placeholder")
        report = gate.check(self.root)
        self.assertEqual(report["bundled_ffmpeg"], "present")
        self.assertEqual(report["counts"]["bundled_ffmpeg"], 1)
        self.assertIn("BUNDLED_FFMPEG_BINARY_REQUIRES_VERIFIED_LICENSE_AND_PROVENANCE", report["reasons"])
        self.assertIn("BUNDLED_FFMPEG_LICENSE_EVIDENCE_MISSING", report["reasons"])
        self.assertFalse(report["release_authorized"])

    def test_symlink_never_follows_outside_fixture(self):
        outside = Path(self.tmp.name) / "outside-private.txt"
        outside.write_text("DO_NOT_DISCLOSE_SYMLINK_TARGET", encoding="utf-8")
        link = self.root / "media-reference.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("platform disallows synthetic symlinks: " + type(exc).__name__)
        report = gate.check(self.root)
        self.assertIn("SYMLINK_PRESENT", report["reasons"])
        self.assertIsNone(report["candidate_digest_sha256"])
        self.assertNotIn(str(outside), json.dumps(report))
        self.assertNotIn("DO_NOT_DISCLOSE", json.dumps(report))

    def test_dirty_repo_cannot_be_released(self):
        report = gate.check(self.root)
        self.assertIn("DIRTY_WORKTREE", report["reasons"])
        self.assertFalse(report["release_authorized"])

    def test_injected_private_file_not_read_and_not_echoed(self):
        secret = "SUPER_SECRET_PRIVATE_PAYLOAD_NEVER_PRINT_THIS"
        self.put("sessions/account-token.json", secret)
        report = gate.check(self.root)
        self.assertIn("PRIVATE_PAYLOAD_INCLUDED", report["reasons"])
        self.assertEqual(report["counts"]["private_payloads"], 1)
        self.assertIsNone(report["candidate_digest_sha256"])
        output = json.dumps(report, sort_keys=True)
        self.assertNotIn(secret, output)
        self.assertNotIn(str(self.root), output)
        self.assertNotIn("account-token", output)

    def test_runtime_payload_in_candidate_holds(self):
        self.put("dist/wahu-runtime.log", "runtime material")
        report = gate.check(self.root)
        self.assertIn("RUNTIME_PAYLOAD_INCLUDED", report["reasons"])
        self.assertIsNone(report["candidate_digest_sha256"])

    def test_owner_digest_mismatch_is_not_approval(self):
        current = gate.check(self.root)
        self.assertRegex(current["candidate_digest_sha256"], r"^[0-9a-f]{64}$")
        report = gate.check(self.root, "0" * 64)
        self.assertIn("OWNER_APPROVAL_DIGEST_MISMATCH_OR_UNVERIFIABLE", report["reasons"])
        self.assertEqual(report["decision"], "HOLD")
        matching = gate.check(self.root, current["candidate_digest_sha256"])
        self.assertIn("OWNER_APPROVAL_UNAUTHENTICATED_NOT_A_LICENSE", matching["reasons"])
        self.assertFalse(matching["release_authorized"])
        self.put("README.md", "# Candidate changed after owner digest was recorded\n")
        changed = gate.check(self.root, current["candidate_digest_sha256"])
        self.assertIn("OWNER_APPROVAL_DIGEST_MISMATCH_OR_UNVERIFIABLE", changed["reasons"])
        self.assertNotEqual(changed["candidate_digest_sha256"], current["candidate_digest_sha256"])

    def test_deterministic_redacted_cli_json_and_text_nonzero(self):
        cmd = [sys.executable, "-B", str(SCRIPT), "--root", str(self.root)]
        first = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        second = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        self.assertEqual(first.returncode, 2)
        self.assertEqual(second.returncode, 2)
        self.assertEqual(first.stdout, second.stdout)
        report = json.loads(first.stdout)
        self.assertEqual(report["schema"], gate.SCHEMA)
        self.assertFalse(report["release_authorized"])
        self.assertNotIn(str(self.root), first.stdout)
        as_text = subprocess.run(cmd + ["--format", "text"], capture_output=True, text=True, timeout=20)
        self.assertEqual(as_text.returncode, 2)
        self.assertIn("PUBLIC_RELEASE_HOLD", as_text.stdout)
        self.assertIn("HOLD_REASON=ROOT_PROJECT_LICENSE_MISSING", as_text.stdout)
        self.assertNotIn(str(self.root), as_text.stdout)


if __name__ == "__main__":
    unittest.main()
