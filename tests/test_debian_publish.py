"""Contract checks for the caller-side Debian submission action."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "debian-publish" / "publish.py"
spec = importlib.util.spec_from_file_location("debian_publish", SCRIPT)
publisher = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(publisher)


class DebianPublishContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        source = root / "package"
        control = source / "DEBIAN"
        control.mkdir(parents=True)
        (control / "control").write_text(
            "Package: sima-cli\nVersion: 2.1.18\nArchitecture: amd64\n"
            "Maintainer: SiMa.ai <dev@sima.ai>\nDescription: SiMa CLI\n"
        )
        (source / "usr/share/sima-cli").mkdir(parents=True)
        (source / "usr/share/sima-cli/payload").write_text("wheel bytes")
        self.artifacts = root / "artifacts"
        self.artifacts.mkdir()
        self.deb = self.artifacts / "sima-cli.deb"
        subprocess.run(["dpkg-deb", "--build", str(source), str(self.deb)], check=True, capture_output=True)
        self.env = {
            "MODE": "validate",
            "PACKAGE_DIRECTORY": str(self.artifacts),
            "CHANNEL": "official",
            "SUITE": "bookworm",
            "ARCHITECTURE": "amd64",
            "EXPECTED_SHA256": publisher.digest(self.deb),
            "PACKAGE_NAME": "sima-cli",
            "PACKAGE_VERSION": "2.1.18",
            "SOURCE_REPOSITORY": "sima-neat/sima-cli",
            "SOURCE_REF": "refs/tags/v2.1.18",
            "SOURCE_COMMIT": "a" * 40,
            "BUILD_SEQUENCE": "12345.1",
            "BUILD_PROVENANCE": json.dumps({"build": "wheel-123"}),
            "LIFECYCLE_CLASS": "official",
            "CHANNEL_URL": "https://debian.stg.neat.sima.ai/official",
            "ARCHIVE_KEY_FINGERPRINT": "B" * 40,
            "ARCHIVE_KEY_URL": "https://debian.stg.neat.sima.ai/keys/official-2026.asc",
            "GITHUB_REPOSITORY": "sima-neat/sima-cli",
            "GITHUB_REF": "refs/tags/v2.1.18",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_RUN_ID": "12345",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_EVENT_NAME": "push",
        }

    def validate(self):
        with patch.dict(os.environ, self.env):
            return publisher.validate()

    def test_official_release(self):
        package, record = self.validate()
        self.assertEqual(package, self.deb)
        self.assertEqual(record["package_version"], "2.1.18")
        self.assertEqual(record["source_repository"], "sima-neat/sima-cli")

    def test_develop_branch_has_collision_resistant_token(self):
        ref = "refs/heads/feature/Add-CLI"
        token = publisher.branch_token(ref)
        self.assertNotEqual(token, publisher.branch_token("refs/heads/feature/add_cli"))
        version = f"2.1.18~dev.{token}.12345.1.{'a' * 12}"
        source = Path(self.temp.name) / "package/DEBIAN/control"
        source.write_text(source.read_text().replace("Version: 2.1.18", f"Version: {version}"))
        subprocess.run(["dpkg-deb", "--build", str(Path(self.temp.name) / "package"), str(self.deb)],
                       check=True, capture_output=True)
        self.env.update(CHANNEL="develop", SOURCE_REF=ref, GITHUB_REF=ref,
                        PACKAGE_VERSION=version, LIFECYCLE_CLASS="branch",
                        EXPECTED_SHA256=publisher.digest(self.deb),
                        CHANNEL_URL="https://debian.stg.neat.sima.ai/develop")
        _, record = self.validate()
        self.assertEqual(record["branch_token"], token)

    def test_rejects_forged_repository_and_pull_request(self):
        self.env["SOURCE_REPOSITORY"] = "sima-neat/other"
        with self.assertRaisesRegex(ValueError, "source repository"):
            self.validate()
        self.env["SOURCE_REPOSITORY"] = "sima-neat/sima-cli"
        self.env["GITHUB_EVENT_NAME"] = "pull_request"
        with self.assertRaisesRegex(ValueError, "trusted push"):
            self.validate()

    def test_rejects_altered_package_and_control_mismatch(self):
        self.deb.write_bytes(self.deb.read_bytes() + b"altered")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            self.validate()
        subprocess.run(["dpkg-deb", "--build", str(Path(self.temp.name) / "package"), str(self.deb)],
                       check=True, capture_output=True)
        self.env["EXPECTED_SHA256"] = publisher.digest(self.deb)
        self.env["PACKAGE_NAME"] = "other"
        with self.assertRaisesRegex(ValueError, "control"):
            self.validate()

    def test_rejects_official_branch_and_develop_tag(self):
        self.env["GITHUB_REF"] = self.env["SOURCE_REF"] = "refs/heads/main"
        with self.assertRaisesRegex(ValueError, "official requires"):
            self.validate()
        self.env["CHANNEL"] = "develop"
        self.env["GITHUB_REF"] = self.env["SOURCE_REF"] = "refs/tags/v2.1.18"
        with self.assertRaisesRegex(ValueError, "develop requires"):
            self.validate()

    def test_rejects_extra_artifact_file(self):
        (self.artifacts / "other.txt").write_text("surprise")
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.validate()

    def test_idempotency_key_changes_with_digest(self):
        _, first = self.validate()
        self.assertEqual(len(first["submission_id"]), 32)
        self.assertEqual(first["submission_id"], self.validate()[1]["submission_id"])
        (Path(self.temp.name) / "package/usr/share/sima-cli/payload").write_text("new wheel bytes")
        subprocess.run(["dpkg-deb", "--build", str(Path(self.temp.name) / "package"), str(self.deb)],
                       check=True, capture_output=True)
        self.env["EXPECTED_SHA256"] = publisher.digest(self.deb)
        _, second = self.validate()
        self.assertNotEqual(first["submission_id"], second["submission_id"])


if __name__ == "__main__":
    unittest.main()
