import unittest

from scripts.pr_sanitization_check import DEFAULT_ALLOWED_BASES, validate


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

    def test_rejects_main_target(self):
        result = validate("main", "Fixes #123", DEFAULT_ALLOWED_BASES)
        self.assertFalse(result.ok)
        self.assertIn("targets 'main'", result.messages[0])

    def test_rejects_missing_issue_reference(self):
        result = validate("develop", "Implementation details only.", DEFAULT_ALLOWED_BASES)
        self.assertFalse(result.ok)
        self.assertIn("does not reference a GitHub issue", result.messages[0])

    def test_reports_both_errors(self):
        result = validate("main", "Implementation details only.", DEFAULT_ALLOWED_BASES)
        self.assertFalse(result.ok)
        self.assertEqual(len(result.messages), 2)


if __name__ == "__main__":
    unittest.main()
