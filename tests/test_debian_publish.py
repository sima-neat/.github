"""Contract checks for the caller-side Debian submission action."""

from __future__ import annotations

import datetime as dt
import http.client
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
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

    def test_accepts_case_insensitive_debian_control_fields(self):
        control = Path(self.temp.name) / "package/DEBIAN/control"
        control.write_text(
            "package: sima-cli\nVERSION: 2.1.18\narchitecture: amd64\n"
            "maintainer: SiMa.ai <dev@sima.ai>\ndescription: SiMa CLI\n"
        )
        subprocess.run(["dpkg-deb", "--build", str(Path(self.temp.name) / "package"), str(self.deb)],
                       check=True, capture_output=True)
        self.env["EXPECTED_SHA256"] = publisher.digest(self.deb)
        self.assertEqual(self.validate()[1]["package_name"], "sima-cli")

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

    def test_same_package_in_two_suites_has_distinct_submission_ids(self):
        _, bookworm = self.validate()
        self.env["SUITE"] = "agate"
        _, agate = self.validate()
        self.assertNotEqual(bookworm["submission_id"], agate["submission_id"])
        self.assertEqual(agate["submission_id"], self.validate()[1]["submission_id"])

    def test_release_field_requires_one_exact_field(self):
        with self.assertRaisesRegex(ValueError, "exactly one Suite"):
            publisher.release_field("X-Suite: bookworm\n", "Suite")
        with self.assertRaisesRegex(ValueError, "exactly one Suite"):
            publisher.release_field("Suite: bookworm\nSuite: agate\n", "Suite")

    def test_public_verification_checks_signed_index_filename(self):
        _, record = self.validate()
        home = Path(self.temp.name) / "gnupg"
        home.mkdir(mode=0o700)
        gpg_env = {**os.environ, "GNUPGHOME": str(home)}
        subprocess.run(["gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", "",
                        "--quick-gen-key", "Vulcan Test <test@example.com>", "ed25519", "sign", "0"],
                       env=gpg_env, check=True, capture_output=True)
        listing = subprocess.run(["gpg", "--batch", "--with-colons", "--list-keys"], env=gpg_env,
                                 check=True, capture_output=True, text=True).stdout
        fingerprint = next(line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:"))
        key = Path(self.temp.name) / "key.asc"
        key.write_bytes(subprocess.run(["gpg", "--batch", "--armor", "--export", fingerprint],
                                       env=gpg_env, check=True, capture_output=True).stdout)
        channel_url = "https://debian.stg.neat.sima.ai/official"
        key_url = "https://debian.stg.neat.sima.ai/keys/official-2026.asc"
        package_url = f"{channel_url}/pool/bookworm/s/sima-cli/sima-cli_2.1.18_amd64.deb"
        filename = "pool/bookworm/s/sima-cli/sima-cli_2.1.18_amd64.deb"
        packages = Path(self.temp.name) / "Packages"
        release = Path(self.temp.name) / "Release"
        inrelease = Path(self.temp.name) / "InRelease"
        def sign_index(index_filename, *, suite="bookworm", label="SiMa.ai official", arches=("amd64",),
                       expired=False, extra_fields=""):
            packages.write_text(
                f"Package: sima-cli\nVersion: 2.1.18\nArchitecture: {record['architecture']}\n"
                f"Filename: {index_filename}\nSize: {self.deb.stat().st_size}\n"
                f"SHA256: {record['sha256']}\n{extra_fields}\n"
            )
            expiry = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=-1 if expired else 1)).strftime("%a, %d %b %Y %H:%M:%S UTC")
            release.write_text(
                f"Label: {label}\nSuite: {suite}\nValid-Until: {expiry}\nSHA256:\n"
                 + "".join(f" {publisher.digest(packages)} {packages.stat().st_size} main/binary-{arch}/Packages\n" for arch in arches)
            )
            subprocess.run(["gpg", "--batch", "--yes", "--local-user", fingerprint, "--clearsign",
                            "--output", str(inrelease), str(release)], env=gpg_env,
                           check=True, capture_output=True)
        sign_index(filename)
        urls = {
            key_url: key,
            f"{channel_url}/dists/bookworm/InRelease": inrelease,
            f"{channel_url}/dists/bookworm/main/binary-amd64/Packages": packages,
            package_url: self.deb,
        }
        def copy_download(url, destination):
            shutil.copyfile(urls[url], destination)
        result = {**{key: record[key] for key in
                     ("suite", "architecture", "package_name", "package_version", "source_commit", "branch_token")},
                  "channel_url": channel_url, "sha256": record["sha256"], "package_url": package_url}
        verify_env = {**self.env, "GNUPGHOME": str(home), "ARCHIVE_KEY_FINGERPRINT": fingerprint}
        with patch.dict(os.environ, verify_env), patch.object(publisher, "download", side_effect=copy_download):
            publisher.verify_public(record, result, self.deb)
            sign_index("pool/bookworm/s/sima-cli/different.deb")
            with self.assertRaisesRegex(ValueError, "misdirected"):
                publisher.verify_public(record, result, self.deb)
            sign_index(filename, suite="bookworm-updates")
            with self.assertRaisesRegex(ValueError, "suite mismatch"):
                publisher.verify_public(record, result, self.deb)
            sign_index(filename, label="SiMa.ai official-extra")
            with self.assertRaisesRegex(ValueError, "label mismatch"):
                publisher.verify_public(record, result, self.deb)
            for duplicate in (f"Filename: pool/bookworm/s/sima-cli/different.deb\n",
                              f"SHA256: {'0' * 64}\n"):
                sign_index(filename, extra_fields=duplicate)
                with self.assertRaisesRegex(ValueError, "duplicate field in Packages stanza"):
                    publisher.verify_public(record, result, self.deb)

            sign_index(filename, expired=True)
            with self.assertRaisesRegex(ValueError, "expired"):
                publisher.verify_public(record, result, self.deb)
            sign_index(filename)
            packages.write_text(packages.read_text() + "corrupted")
            with self.assertRaisesRegex(ValueError, "does not match signed Release"):
                publisher.verify_public(record, result, self.deb)
            sign_index(filename)
            with patch.dict(os.environ, {"ARCHIVE_KEY_FINGERPRINT": "0" * 40}), \
                 self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
                publisher.verify_public(record, result, self.deb)
            bad_package = Path(self.temp.name) / "tampered.deb"
            bad_package.write_bytes(b"not the submitted bytes")
            urls[package_url] = bad_package
            with self.assertRaisesRegex(ValueError, "differs from submitted"):
                publisher.verify_public(record, result, self.deb)
            urls[package_url] = self.deb
            record["architecture"] = result["architecture"] = "all"
            urls[f"{channel_url}/dists/bookworm/main/binary-arm64/Packages"] = packages
            sign_index(filename, arches=("amd64", "arm64"))
            publisher.verify_public(record, result, self.deb)
            sign_index(filename, arches=("amd64",))
            with self.assertRaisesRegex(ValueError, "does not index arm64"):
                publisher.verify_public(record, result, self.deb)

    def test_retries_transient_signature_verification_failure(self):
        signature_failure = subprocess.CalledProcessError(1, ["gpgv", "InRelease"])
        with patch.object(publisher, "verify_public", side_effect=[signature_failure, None]) as verify, \
             patch.object(publisher.time, "sleep") as sleep:
            publisher.verify_public_until({}, {}, self.deb, publisher.time.monotonic() + 60)
        self.assertEqual(verify.call_count, 2)
        sleep.assert_called_once_with(10)

    def test_persistent_signature_failure_still_fails(self):
        signature_failure = subprocess.CalledProcessError(1, ["gpgv", "InRelease"])
        with patch.object(publisher, "verify_public", side_effect=signature_failure), \
             patch.object(publisher.time, "monotonic", return_value=100), \
             self.assertRaisesRegex(RuntimeError, "did not converge"):
            publisher.verify_public_until({}, {}, self.deb, 100)

    def test_retries_response_body_timeout(self):
        with patch.object(publisher, "verify_public", side_effect=[TimeoutError("body stalled"), None]) as verify, \
             patch.object(publisher.time, "sleep") as sleep:
            publisher.verify_public_until({}, {}, self.deb, publisher.time.monotonic() + 60)
        self.assertEqual(verify.call_count, 2)
        sleep.assert_called_once_with(10)

    def test_retries_interrupted_response_body(self):
        failures = (http.client.IncompleteRead(b"partial", 10),
                    ConnectionResetError("connection reset"))
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), \
                 patch.object(publisher, "verify_public", side_effect=[failure, None]) as verify, \
                 patch.object(publisher.time, "sleep") as sleep:
                publisher.verify_public_until({}, {}, self.deb, publisher.time.monotonic() + 60)
                self.assertEqual(verify.call_count, 2)
                sleep.assert_called_once_with(10)

    def test_successful_execution_gets_full_public_verification_window(self):
        _, record = self.validate()
        def fake_aws(service, operation, *args):
            if operation == "start-execution":
                return {"executionArn": "arn:aws:states:us-west-2:123456789012:execution:intake:test"}
            if operation == "describe-execution":
                return {"status": "SUCCEEDED", "output": "{}"}
            return {}
        env = {**self.env, "INTAKE_BUCKET": "vulcan-apt-intake",
               "INTAKE_STATE_MACHINE_ARN": "arn:aws:states:us-west-2:123456789012:stateMachine:intake"}
        with patch.dict(os.environ, env), patch.object(publisher, "aws", side_effect=fake_aws), \
             patch.object(publisher.time, "monotonic", side_effect=[0, 1740, 1740]), \
             patch.object(publisher, "verify_public_until") as verify:
            publisher.submit(self.deb, record)
        self.assertEqual(verify.call_args.args[3], 1740 + 3 * 60)

    def test_submission_uses_immutable_scoped_objects_and_fixed_execution_input(self):
        _, record = self.validate()
        calls = []
        def fake_aws(service, operation, *args):
            calls.append((service, operation, args))
            if operation == "put-object":
                self.assertEqual(args[args.index("--if-none-match") + 1], "*")
                key = args[args.index("--key") + 1]
                self.assertTrue(key.startswith(f"submissions/{record['source_repository']}/official/{record['submission_id']}/"))
                if key.endswith("manifest.json"):
                    manifest = json.loads(Path(args[args.index("--body") + 1]).read_text())
                    self.assertEqual(manifest, record)
            if operation == "start-execution":
                execution_input = json.loads(args[args.index("--input") + 1])
                self.assertEqual(set(execution_input), {"schema_version", "manifest_bucket", "manifest_key", "package_key", "submission_id"})
                return {"executionArn": "execution"}
            if operation == "describe-execution":
                return {"status": "SUCCEEDED", "output": "{}"}
            return {}
        env = {**self.env, "INTAKE_BUCKET": "vulcan-apt-intake",
               "INTAKE_STATE_MACHINE_ARN": "arn:aws:states:us-west-2:123456789012:stateMachine:intake"}
        with patch.dict(os.environ, env), patch.object(publisher, "aws", side_effect=fake_aws), \
             patch.object(publisher, "verify_public_until") as verify:
            publisher.submit(self.deb, record)
        self.assertEqual([call[1] for call in calls], ["put-object", "put-object", "start-execution", "describe-execution"])
        verify.assert_called_once()

    def test_failed_execution_never_reports_success(self):
        _, record = self.validate()
        env = {**self.env, "INTAKE_BUCKET": "vulcan-apt-intake",
               "INTAKE_STATE_MACHINE_ARN": "arn:aws:states:us-west-2:123456789012:stateMachine:intake"}
        for status in ("FAILED", "TIMED_OUT", "ABORTED"):
            def fake_aws(service, operation, *args, status=status):
                if operation == "start-execution":
                    return {"executionArn": "execution"}
                if operation == "describe-execution":
                    return {"status": status}
                return {}
            with self.subTest(status=status), patch.dict(os.environ, env), \
                 patch.object(publisher, "aws", side_effect=fake_aws), \
                 patch.object(publisher, "verify_public_until") as verify:
                with self.assertRaisesRegex(RuntimeError, status):
                    publisher.submit(self.deb, record)
                verify.assert_not_called()

    def test_execution_timeout_never_verifies_publication(self):
        _, record = self.validate()
        env = {**self.env, "INTAKE_BUCKET": "vulcan-apt-intake",
               "INTAKE_STATE_MACHINE_ARN": "arn:aws:states:us-west-2:123456789012:stateMachine:intake"}
        with patch.dict(os.environ, env), \
             patch.object(publisher, "aws", return_value={"executionArn": "execution"}), \
             patch.object(publisher.time, "monotonic", side_effect=[0, 1800]), \
             patch.object(publisher, "verify_public_until") as verify:
            with self.assertRaisesRegex(TimeoutError, "30 minutes"):
                publisher.submit(self.deb, record)
            verify.assert_not_called()

    def test_submission_failure_does_not_write_outputs(self):
        output = Path(self.temp.name) / "outputs"
        with patch.dict(os.environ, {**self.env, "MODE": "submit", "GITHUB_OUTPUT": str(output)}), \
             patch.object(publisher, "submit", side_effect=RuntimeError("verification failed")), \
             self.assertRaisesRegex(RuntimeError, "verification failed"):
            publisher.main()
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
