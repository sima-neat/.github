import argparse
import json
import tempfile
import unittest
from pathlib import Path

from scripts.pr_sanitization_check import (
    DEFAULT_ALLOWED_BASES,
    DEFAULT_ALLOWED_MAIN_SOURCES,
    _resolve_pr_fields,
    validate,
)


class PrSanitizationCheckTest(unittest.TestCase):
    def test_allows_develop_with_local_issue_reference(self):
        result = validate("develop", "Fixes #123", DEFAULT_ALLOWED_BASES)
        self.assertTrue(result.ok)

    def test_allows_release_branch_with_cross_repo_reference(self):
        result = validate("release-2.1", "Refs sima-neat/core#443", DEFAULT_ALLOWED_BASES)
        self.assertTrue(result.ok)

    def test_allows_integration_branch_with_full_issue_url(self):
        result = validate(
            "integration/0.2.1",
            "https://github.com/sima-neat/core/issues/443",
            DEFAULT_ALLOWED_BASES,
        )
        self.assertTrue(result.ok)

    def test_allows_develop_to_main_without_issue_reference(self):
        result = validate(
            "main",
            "Release changelog only.",
            DEFAULT_ALLOWED_BASES,
            head_ref="develop",
            head_repo="sima-neat/insight",
            base_repo="sima-neat/insight",
        )
        self.assertTrue(result.ok)

    def test_allows_release_branch_to_main_without_issue_reference(self):
        result = validate(
            "main",
            "Release changelog only.",
            DEFAULT_ALLOWED_BASES,
            head_ref="release-2.1",
            head_repo="sima-neat/insight",
            base_repo="sima-neat/insight",
        )
        self.assertTrue(result.ok)

    def test_rejects_fork_develop_branch_to_main(self):
        result = validate(
            "main",
            "Release changelog only.",
            DEFAULT_ALLOWED_BASES,
            head_ref="develop",
            head_repo="external/insight",
            base_repo="sima-neat/insight",
        )
        self.assertFalse(result.ok)
        self.assertEqual(len(result.messages), 2)
        self.assertIn("fork repository 'external/insight'", result.messages[0])
        self.assertIn("does not reference a GitHub issue", result.messages[1])

    def test_rejects_feature_branch_to_main(self):
        result = validate(
            "main",
            "Fixes #123",
            DEFAULT_ALLOWED_BASES,
            head_ref="feature/test",
            head_repo="sima-neat/insight",
            base_repo="sima-neat/insight",
        )
        self.assertFalse(result.ok)
        self.assertIn("targets 'main' from 'feature/test'", result.messages[0])

    def test_rejects_main_target_without_source_branch(self):
        result = validate("main", "Fixes #123", DEFAULT_ALLOWED_BASES)
        self.assertFalse(result.ok)
        self.assertIn("Unable to determine the pull request source branch", result.messages[0])

    def test_rejects_missing_issue_reference(self):
        result = validate("develop", "Implementation details only.", DEFAULT_ALLOWED_BASES)
        self.assertFalse(result.ok)
        self.assertIn("does not reference a GitHub issue", result.messages[0])

    def test_reports_both_errors(self):
        result = validate("production", "Implementation details only.", DEFAULT_ALLOWED_BASES)
        self.assertFalse(result.ok)
        self.assertEqual(len(result.messages), 2)

    def test_default_main_source_patterns_are_develop_and_release(self):
        self.assertEqual(DEFAULT_ALLOWED_MAIN_SOURCES, ("develop", "release-*"))

    def test_resolves_head_and_base_repo_from_event_payload(self):
        event = {
            "pull_request": {
                "base": {
                    "ref": "main",
                    "repo": {"full_name": "sima-neat/insight"},
                },
                "head": {
                    "ref": "develop",
                    "repo": {"full_name": "external/insight"},
                },
                "body": "Release changelog only.",
            }
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            event_path = Path(tmp_dir) / "event.json"
            event_path.write_text(json.dumps(event), encoding="utf-8")
            args = argparse.Namespace(
                event_path=str(event_path),
                base_ref="",
                head_ref="",
                base_repo="",
                head_repo="",
                body=None,
            )

            base_ref, head_ref, base_repo, head_repo, body = _resolve_pr_fields(args)

        self.assertEqual(base_ref, "main")
        self.assertEqual(head_ref, "develop")
        self.assertEqual(base_repo, "sima-neat/insight")
        self.assertEqual(head_repo, "external/insight")
        self.assertEqual(body, "Release changelog only.")


if __name__ == "__main__":
    unittest.main()
